"""
Context Compression System - Automatic and manual conversation compression.
"""
from __future__ import annotations
import copy
import logging
import json
import time
import uuid
import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any

from providers.base import Message

logger = logging.getLogger(__name__)

# Try to use tiktoken for accurate token counting; fallback to rough estimate
_TIKTOKEN_AVAILABLE = False
_ENCODING = None
try:
    import tiktoken
    _ENCODING = tiktoken.get_encoding("cl100k_base")
    _TIKTOKEN_AVAILABLE = True
except Exception as exc:
    logger.info(
        "tiktoken 不可用，将使用粗略估算（1 token ≈ 4 字符）: %s",
        exc,
    )


@dataclass
class CompressedTranscript:
    id: str = ""
    summary: str = ""
    message_count: int = 0
    original_token_estimate: int = 0
    created_at: float = field(default_factory=time.time)
    store_path: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'summary': self.summary,
            'message_count': self.message_count,
            'original_token_estimate': self.original_token_estimate,
            'created_at': self.created_at,
            'store_path': self.store_path,
        }


class Compressor:
    """Manages conversation context compression.

    If an LLM provider is injected via ``set_provider()``, summarization
    uses real AI-generated summaries.  Otherwise falls back to a
    lightweight statistical summary.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        cfg = config or {}
        self.token_threshold = cfg.get('token_threshold', 100000)
        self.max_transcripts = max(0, int(cfg.get('max_transcripts', 100)))
        self._transcripts: Dict[str, CompressedTranscript] = {}
        self._transcript_dir: Optional[Path] = None
        self._provider: Any = None  # LLMProvider for real summarization

        transcript_dir = cfg.get('transcript_dir', '')
        if transcript_dir:
            self._transcript_dir = Path(transcript_dir)
            self._transcript_dir.mkdir(parents=True, exist_ok=True)
            self._load_persisted_transcripts()
            self._prune_old_transcripts()

    def set_provider(self, provider: Any) -> None:
        """Inject an LLM provider for AI-powered summarization.

        Called by MiniClaudeAgent after provider setup is complete.
        Without a provider, summarization falls back to statistical counts.
        """
        self._provider = provider
        logger.info("Compressor: LLM provider injected for real summarization")

    # ── Token Estimation ──────────────────────────────────────

    def estimate_tokens(self, messages: List[Message]) -> int:
        """Accurate token count using tiktoken (cl100k_base). Falls back to 1 token ≈ 4 characters."""
        if _TIKTOKEN_AVAILABLE and _ENCODING is not None:
            total = 0
            for msg in messages:
                total += len(_ENCODING.encode(msg.content or ""))
                total += len(_ENCODING.encode(self._message_metadata_text(msg)))
                # Add overhead for message role formatting (~4 tokens per message)
                total += 4
            return total
        # Fallback: rough estimate
        total_chars = sum(
            len(m.content or "") + len(self._message_metadata_text(m))
            for m in messages
        )
        return total_chars // 4

    @staticmethod
    def _message_metadata_text(message: Message) -> str:
        """Serialize persisted message fields that are not part of ``content``."""
        metadata: Dict[str, Any] = {}
        if message.name:
            metadata['name'] = message.name
        if message.tool_calls:
            metadata['tool_calls'] = message.tool_calls
        if message.tool_call_id:
            metadata['tool_call_id'] = message.tool_call_id
        if not metadata:
            return ""
        try:
            return json.dumps(metadata, ensure_ascii=False, sort_keys=True, default=str)
        except (TypeError, ValueError):
            return str(metadata)

    def estimate_tokens_for_text(self, text: str) -> int:
        """Accurate token count for a single text string."""
        if _TIKTOKEN_AVAILABLE and _ENCODING is not None:
            return len(_ENCODING.encode(text or ""))
        return len(text or "") // 4

    # ── Compression Decision ──────────────────────────────────

    def should_compress(self, messages: List[Message]) -> bool:
        """Check if messages exceed the token threshold."""
        return self.estimate_tokens(messages) > self.token_threshold

    def should_microcompact(self, messages: List[Message]) -> bool:
        """Check if rapid growth suggests micro-compaction."""
        return (
            self.estimate_tokens(messages) > self.token_threshold * 0.7
            and self._has_microcompact_candidates(messages)
        )

    def _has_microcompact_candidates(self, messages: List[Message]) -> bool:
        """Return whether micro-compaction can make a meaningful change."""
        if len(messages) < 10:
            return False

        protect_start = max(len(messages) - 6, 2)
        todo_count = 0
        for i, msg in enumerate(messages):
            if i < 2 or i >= protect_start or msg.role != 'tool':
                continue

            tool_name = self._infer_tool_name(messages, i)
            if tool_name in ('bash', 'search_code', 'count_occurrences'):
                if len(msg.content or '') > 300:
                    return True
            elif tool_name == 'TodoWrite':
                todo_count += 1
            elif tool_name in ('read_file', 'edit_file'):
                if len(msg.content or '') > 5000:
                    return True

        return todo_count > 1

    # ── Compression Actions ───────────────────────────────────

    def microcompact(self, messages: List[Message]) -> List[Message]:
        """
        基于"存根替换（Stubbing）"的微压缩：保留所有消息节点和 tool_call_id，
        仅替换 ``content`` 为精简存根以释放 Token。

        三层淘汰（Tiered Eviction）：
          Tier 1（重型输出）—— 截断 bash/search_code/count_occurrences 的长内容
          状态折叠（State Folding）—— 只保留最新一条 TodoWrite 的完整内容
          Tier 3（核心资产）—— 对过长的 read_file/edit_file 保留首尾各 500 字符
        """
        if len(messages) < 10:
            return messages

        # 保护前 2 条（system + 首条 user）和最近的 6 条消息
        protect_start = max(len(messages) - 6, 2)

        # 收集 TodoWrite 工具消息的索引（用于状态折叠）
        todo_indices: List[int] = []

        for i, msg in enumerate(messages):
            if i < 2 or i >= protect_start:
                continue
            if msg.role != 'tool':
                continue

            tool_name = self._infer_tool_name(messages, i)

            # ── Tier 1：重型标准输出工具 ──
            if tool_name in ('bash', 'search_code', 'count_occurrences'):
                if len(msg.content or '') > 300:
                    status = self._infer_tool_execution_status(msg.content)
                    if status is True:
                        status_text = "Execution completed; output details were omitted."
                    elif status is False:
                        status_text = "Execution failed; output details were omitted."
                    else:
                        status_text = (
                            "Execution status was not encoded in this message; "
                            "output details were omitted."
                        )
                    msg.content = (
                        "[System: Tool output truncated to save context window. "
                        f"{status_text}]"
                    )
                continue

            # ── 状态折叠：TodoWrite 只保留时间线上最后一次 ──
            if tool_name == 'TodoWrite':
                todo_indices.append(i)
                continue

            # ── Tier 3：核心资产（read_file / edit_file） ──
            if tool_name in ('read_file', 'edit_file') and len(msg.content or '') > 5000:
                head = msg.content[:500]
                tail = msg.content[-500:]
                msg.content = (
                    f"{head}\n...\n"
                    "[System: Middle content omitted during micro-compaction]\n"
                    f"...\n{tail}"
                )
                continue

        # 折叠状态：之前所有旧 TodoWrite 替换为存根
        for idx in todo_indices[:-1]:
            messages[idx].content = "[System: State superseded by newer TodoWrite.]"

        return messages

    @staticmethod
    def _infer_tool_execution_status(content: str) -> Optional[bool]:
        """Read only explicit exit-code facts; never infer status from tool name."""
        first_line = (content or '').splitlines()[0] if content else ''
        match = re.match(
            r'^\[(?:Command executed with exit code|Exit Code:)\s*(-?\d+)\]',
            first_line,
        )
        if not match:
            return None
        return int(match.group(1)) == 0

    @staticmethod
    def _infer_tool_name(messages: List[Message], tool_idx: int) -> str:
        """
        通过匹配 ``tool_call_id`` 与前一条 ``assistant`` 消息的 ``tool_calls``，
        推断指定 ``tool`` 消息是由哪个工具产生的。
        """
        tool_msg = messages[tool_idx]
        if not tool_msg.tool_call_id:
            return ""
        for i in range(tool_idx - 1, -1, -1):
            msg = messages[i]
            if msg.role == 'assistant' and msg.tool_calls:
                for tc in msg.tool_calls:
                    if not isinstance(tc, dict):
                        continue
                    if tc.get('id', '') == tool_msg.tool_call_id:
                        fn = tc.get('function', {})
                        if isinstance(fn, dict):
                            return fn.get('name', '')
        return ""

    def compress(self, messages: List[Message]) -> List[Message]:
        """
        Full compression: summarize early conversation and replace
        with a system message containing the summary.
        Safely preserves tool_calls→tool message chains.
        """
        if len(messages) < 4:
            return messages

        token_estimate = self.estimate_tokens(messages)
        message_count = len(messages)

        # Keep first 2 (system + intro) and last 15 messages, but never split
        # an assistant(tool_calls) -> tool result segment at the tail boundary.
        tail_start = max(2, len(messages) - 15)
        if tail_start < len(messages) and messages[tail_start].role == 'tool':
            while tail_start > 2 and messages[tail_start - 1].role == 'tool':
                tail_start -= 1
            if (
                tail_start > 2
                and messages[tail_start - 1].role == 'assistant'
                and messages[tail_start - 1].tool_calls
            ):
                tail_start -= 1

        head = messages[:2]
        tail = list(messages[tail_start:])

        # Summarize the middle
        middle = messages[2:tail_start]
        if not middle:
            # A summary without a source segment would expand the history and
            # create a false compression event on every subsequent turn.
            return messages
        summary = self._generate_summary(middle)
        if not isinstance(summary, str) or not summary.strip():
            logger.warning("Full Compression 未生成可用摘要，保留原始消息")
            return messages

        # Create a compressed transcript record
        transcript = CompressedTranscript(
            id=str(uuid.uuid4())[:8],
            summary=summary,
            message_count=message_count,
            original_token_estimate=token_estimate,
        )

        # Build compressed message list
        compressed = list(head)
        compressed.append(Message(
            role='system',
            content=f"[Compressed conversation summary: {summary}]"
        ))
        compressed.extend(tail)

        # Clean up orphaned tool chains (structural integrity)
        cleaned = self._clean_tool_chains(compressed)
        # Final sanitize pass: guarantee tool_calls↔tool absolute closure
        cleaned = self.sanitize_openai_messages(cleaned)

        # Commit only after the candidate has been built and sanitized.  A
        # failed summary must not destroy source history or create a false
        # successful transcript.
        self._save_transcript(transcript)

        logger.info(
            f"已压缩 {message_count} 条消息（{token_estimate} tokens）-> "
            f"{len(cleaned)} 条消息"
        )
        return cleaned

    @staticmethod
    def sanitize_openai_messages(messages: List[Message]) -> List[Message]:
        """
        严格清洗消息流，确保 tool_calls 和 tool 响应绝对闭环，防止 400 错误。

        处理三种异常场景：
        1. 孤立的 tool 消息（前面没有 assistant(tool_calls)）→ 丢弃
        2. assistant(tool_calls) 后缺少部分 tool 响应 → 截断 tool_calls 只保留有响应的
        3. assistant(tool_calls) 后完全没有 tool 响应 → 剔除 tool_calls，保留文本
        """
        sanitized = []
        i = 0
        while i < len(messages):
            msg = messages[i]

            # 1. 丢弃前方没有 assistant 发起 call 的孤立 tool 消息
            if msg.role == "tool":
                i += 1
                continue

            # 2. 处理包含 tool_calls 的 assistant 消息
            if msg.role == "assistant" and msg.tool_calls:
                tool_responses = {}
                j = i + 1
                # 收集紧跟其后的所有 tool 消息
                while j < len(messages) and messages[j].role == "tool":
                    tool_responses[messages[j].tool_call_id] = messages[j]
                    j += 1

                valid_tool_calls = []
                for tc in msg.tool_calls:
                    tc_id = tc.get('id', '') if isinstance(tc, dict) else getattr(tc, 'id', '')
                    if tc_id in tool_responses:
                        valid_tool_calls.append(tc)

                # 存在合法闭环：保留 assistant + 对应的 tool 响应
                if valid_tool_calls:
                    msg_copy = copy.deepcopy(msg)
                    msg_copy.tool_calls = valid_tool_calls
                    sanitized.append(msg_copy)
                    for tc in valid_tool_calls:
                        tc_id = tc.get('id', '') if isinstance(tc, dict) else getattr(tc, 'id', '')
                        sanitized.append(tool_responses[tc_id])
                else:
                    # 所有工具响应都在压缩中丢失：保留文本，剔除无效 tool_calls
                    if msg.content:
                        msg_copy = copy.deepcopy(msg)
                        msg_copy.tool_calls = None
                        sanitized.append(msg_copy)

                i = j  # 跳过已被打包处理的 tool 消息
                continue

            # 3. 正常保留 user 或普通 assistant 消息
            sanitized.append(msg)
            i += 1

        return sanitized

    @staticmethod
    def _clean_tool_chains(messages: List[Message]) -> List[Message]:
        """
        Ensure valid tool_calls→tool message chains.

        - Removes orphaned tool messages (no matching assistant(tool_calls))
        - Strips tool_calls from assistant messages whose tool responses
          were removed (e.g. by compression)
        """
        # Collect tool_call_ids from both assistant(tool_calls) and tool messages
        from_assistant: set = set()
        from_tool: set = set()
        for msg in messages:
            if msg.role == 'assistant' and msg.tool_calls:
                for tc in msg.tool_calls:
                    if isinstance(tc, dict) and tc.get('id'):
                        from_assistant.add(tc['id'])
            elif msg.role == 'tool' and msg.tool_call_id:
                from_tool.add(msg.tool_call_id)

        # Only IDs that appear in BOTH are valid (otherwise one side was compressed away)
        valid_ids = from_assistant & from_tool

        cleaned = []
        for msg in messages:
            if msg.role == 'tool':
                if msg.tool_call_id in valid_ids:
                    valid_ids.discard(msg.tool_call_id)
                    cleaned.append(msg)
            elif msg.role == 'assistant' and msg.tool_calls:
                valid = [
                    tc for tc in msg.tool_calls
                    if isinstance(tc, dict) and tc.get('id', '') in valid_ids
                ]
                if valid:
                    new = copy.deepcopy(msg)
                    new.tool_calls = valid
                    cleaned.append(new)
                else:
                    cleaned.append(Message(role='assistant', content=msg.content))
            else:
                cleaned.append(msg)

        return cleaned

    # ── Summarization ──────────────────────────────────────────

    def _generate_summary(self, messages: List[Message]) -> Optional[str]:
        """Generate a high-level summary preserving only what's needed for continuity.

        Delegates to an LLM when available.  Statistical summarization is an
        intentional fallback only when no provider is configured; provider
        failures return ``None`` so Full Compression can fail closed.
        """
        # ── Primary: LLM-powered summary ──────────────────────
        if self._provider:
            try:
                return self._llm_summarize(messages)
            except Exception as e:
                logger.warning(f"LLM 总结失败，Full Compression fail-closed: {e}")
                return None

        # ── Fallback: statistical summary ─────────────────────
        return self._statistical_summary(messages)

    def _llm_summarize(self, messages: List[Message]) -> str:
        """Call the LLM to produce a high-level intent summary.

        The summarization request is intentionally small (max 600 output
        tokens, no tools) so it cannot trigger recursive compression or
        runaway token usage.
        """
        # Build a compact text representation of the messages to compress
        lines: List[str] = []
        for m in messages:
            role = m.role
            content = (m.content or "")[:2000]  # per-message cap
            lines.append(f"[{role}]: {content}")
        conv_text = "\n".join(lines)

        # Keep the prompt under ~12K chars to stay within safe bounds
        if len(conv_text) > 12000:
            conv_text = conv_text[-12000:]

        summary_msgs = [
            Message(
                role="user",
                content=(
                    "Summarize the high-level intent and current progress of "
                    "this conversation. Do not attempt to summarize code blocks, "
                    "file contents, or exact IDs. Focus entirely on what has been "
                    "accomplished so far and what the immediate next blocked step "
                    "is. Keep it concise.\n\n"
                    f"{conv_text}"
                ),
            )
        ]

        response = self._provider.create_message(
            summary_msgs,
            max_tokens=600,
            temperature=0.3,
        )
        parsed = self._provider.parse_response(response)
        summary = parsed.get("content", "") if isinstance(parsed, dict) else ""
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError("LLM summary response has no usable content")

        logger.debug(
            f"LLM 总结完成: {len(conv_text)} chars input → "
            f"{len(summary)} chars summary"
        )
        return summary.strip()

    def _statistical_summary(self, messages: List[Message]) -> str:
        """Lightweight summary from message counts and keywords."""
        user_msgs = sum(1 for m in messages if m.role == "user")
        asst_msgs = sum(1 for m in messages if m.role == "assistant")
        tool_msgs = sum(1 for m in messages if m.role in ("tool", "tool_result"))

        # Extract key topics from user messages
        topics: set = set()
        for m in messages:
            if m.role == "user":
                words = m.content.lower().split()[:20]
                for w in words:
                    if len(w) > 4 and w not in topics:
                        topics.add(w)
                        if len(topics) >= 8:
                            break
                if len(topics) >= 8:
                    break

        topic_str = ", ".join(sorted(topics)) if topics else "general conversation"
        return (
            f"{user_msgs} user messages, {asst_msgs} assistant responses, "
            f"{tool_msgs} tool calls. Topics: {topic_str}"
        )

    # ── Transcript Management ─────────────────────────────────

    def _load_persisted_transcripts(self) -> None:
        """Load valid transcript files so retention survives process restarts."""
        if not self._transcript_dir or not self._transcript_dir.exists():
            return

        candidates: Dict[str, List[CompressedTranscript]] = {}
        for path in self._transcript_dir.glob("transcript_*.json"):
            try:
                with path.open('r', encoding='utf-8') as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    raise ValueError("transcript JSON must be an object")

                transcript_id = str(data.get('id') or "").strip()
                summary = data.get('summary')
                if not transcript_id or not isinstance(summary, str):
                    raise ValueError("transcript metadata is incomplete")

                transcript = CompressedTranscript(
                    id=transcript_id,
                    summary=summary,
                    message_count=int(data.get('message_count', 0)),
                    original_token_estimate=int(data.get('original_token_estimate', 0)),
                    created_at=float(data.get('created_at', path.stat().st_mtime)),
                    store_path=str(path),
                )
                filename_id = path.name[len("transcript_"):-len(".json")]
                if filename_id != transcript.id:
                    logger.warning(
                        "Transcript 文件名 ID 与内容 ID 不一致: %s -> %s",
                        path,
                        transcript.id,
                    )
                candidates.setdefault(transcript.id, []).append(transcript)
            except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
                logger.warning("跳过无效 Transcript %s: %s", path, exc)

        for transcript_id, entries in candidates.items():
            # If duplicate artifacts claim one ID, retain the newest valid
            # record (path name is a deterministic tie-breaker) and remove
            # the other managed artifacts.  They must not silently become
            # unmanaged files after the in-memory dict collapses the ID.
            canonical = max(
                entries,
                key=lambda item: (item.created_at, item.store_path or ""),
            )
            self._transcripts[transcript_id] = canonical
            for duplicate in entries:
                if duplicate is canonical or not duplicate.store_path:
                    continue
                try:
                    Path(duplicate.store_path).unlink()
                except OSError as exc:
                    logger.warning(
                        "删除重复 Transcript %s 失败，已从索引排除: %s",
                        duplicate.store_path,
                        exc,
                    )

    def _prune_old_transcripts(self) -> None:
        """Apply retention to both in-memory and persisted transcripts."""
        transcripts = sorted(
            self._transcripts.values(),
            key=lambda transcript: transcript.created_at,
        )
        while len(transcripts) > self.max_transcripts:
            old = transcripts.pop(0)
            if old.store_path:
                try:
                    Path(old.store_path).unlink()
                except OSError as exc:
                    # Keep the index aligned with the durable record when
                    # deletion fails; silently dropping it would make a
                    # later process rediscover the same retained artifact.
                    logger.warning("删除旧 Transcript %s 失败: %s", old.store_path, exc)
                    continue
            self._transcripts.pop(old.id, None)

    def _save_transcript(self, transcript: CompressedTranscript) -> bool:
        """Persist a transcript before publishing it in the in-memory index."""
        if self._transcript_dir:
            path = self._transcript_dir / f"transcript_{transcript.id}.json"
            transcript.store_path = str(path)
            try:
                self._transcript_dir.mkdir(parents=True, exist_ok=True)
                with open(path, 'w', encoding='utf-8') as f:
                    json.dump(transcript.to_dict(), f, indent=2)
            except Exception as e:
                logger.error(f"保存对话记录失败: {e}")
                return False

        self._transcripts[transcript.id] = transcript
        self._prune_old_transcripts()
        return True

    def get_transcripts(self) -> List[CompressedTranscript]:
        return sorted(self._transcripts.values(),
                      key=lambda t: t.created_at, reverse=True)

    def get_compression_stats(self) -> Dict[str, Any]:
        return {
            'total_transcripts': len(self._transcripts),
            'token_threshold': self.token_threshold,
            'max_transcripts': self.max_transcripts,
        }
