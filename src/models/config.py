import os
import yaml
from pathlib import Path
from typing import Dict, Any, Optional
from dataclasses import dataclass, field
from dotenv import load_dotenv

# 自动加载 .env 文件
load_dotenv()


@dataclass
class LLMConfig:
    provider: str = "deepseek"
    model: str = "deepseek-chat"
    max_tokens: int = 8000
    temperature: float = 0.0
    api_key: str = ""
    base_url: str = ""


@dataclass
class AgentConfig:
    name: str = "mini-claude"
    version: str = "1.0.0"


@dataclass
class FeaturesConfig:
    subagent: bool = True
    tasks: bool = True
    compression: bool = True
    background: bool = True
    team: bool = True
    skills: bool = True


@dataclass
class TasksConfig:
    directory: str = ".tasks"
    auto_claim: bool = True
    poll_interval: int = 5


@dataclass
class TeamConfig:
    directory: str = ".team"
    idle_timeout: int = 60
    auto_claim_tasks: bool = True


@dataclass
class CompressionConfig:
    token_threshold: int = 100000
    max_transcripts: int = 100
    microcompact_threshold: int = 3


@dataclass
class BackgroundConfig:
    max_concurrent: int = 5
    default_timeout: int = 120
    notification_queue_size: int = 1000


@dataclass
class SkillsConfig:
    directory: str = "skills"


@dataclass
class LoggingConfig:
    level: str = "INFO"
    file: str = "logs/agent.log"
    format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"


@dataclass
class Config:
    agent: AgentConfig = field(default_factory=AgentConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    features: FeaturesConfig = field(default_factory=FeaturesConfig)
    tasks: TasksConfig = field(default_factory=TasksConfig)
    team: TeamConfig = field(default_factory=TeamConfig)
    compression: CompressionConfig = field(default_factory=CompressionConfig)
    background: BackgroundConfig = field(default_factory=BackgroundConfig)
    skills: SkillsConfig = field(default_factory=SkillsConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)


class ConfigManager:
    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path or Path("configs/default.yaml")
        self.config = self._load_config()

    def _load_config(self) -> Config:
        """Load configuration from YAML file with environment variable substitution."""
        if not self.config_path.exists():
            return Config()

        with open(self.config_path, 'r', encoding='utf-8') as f:
            raw_config = yaml.safe_load(f)

        # Substitute environment variables
        def substitute_env(obj):
            if isinstance(obj, dict):
                return {k: substitute_env(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [substitute_env(item) for item in obj]
            elif isinstance(obj, str) and obj.startswith("${") and obj.endswith("}"):
                env_key = obj[2:-1]
                return os.getenv(env_key, obj)
            else:
                return obj

        processed_config = substitute_env(raw_config)
        return self._dict_to_config(processed_config)

    def _dict_to_config(self, data: Dict[str, Any]) -> Config:
        """Convert dictionary to Config dataclass."""
        config = Config()

        if 'agent' in data:
            config.agent = AgentConfig(**data['agent'])

        if 'llm' in data:
            llm_data = data['llm']
            provider_config = llm_data.copy()
            active_provider = llm_data.get('provider', 'deepseek')

            # Apply provider-specific overrides only for the active provider
            for pname in ('deepseek', 'anthropic'):
                if pname in llm_data:
                    if active_provider == pname:
                        pconfig = llm_data[pname]
                        if pconfig.get('api_key'):
                            provider_config['api_key'] = pconfig['api_key']
                        if pconfig.get('base_url'):
                            provider_config['base_url'] = pconfig['base_url']
                    del provider_config[pname]

            config.llm = LLMConfig(**provider_config)

        if 'features' in data:
            config.features = FeaturesConfig(**data['features'])

        if 'tasks' in data:
            config.tasks = TasksConfig(**data['tasks'])

        if 'team' in data:
            config.team = TeamConfig(**data['team'])

        if 'compression' in data:
            config.compression = CompressionConfig(**data['compression'])

        if 'background' in data:
            config.background = BackgroundConfig(**data['background'])

        if 'skills' in data:
            config.skills = SkillsConfig(**data['skills'])

        if 'logging' in data:
            config.logging = LoggingConfig(**data['logging'])

        return config

    def get_config(self) -> Config:
        """Get the current configuration."""
        return self.config

    def update_config(self, updates: Dict[str, Any]):
        """Update configuration with new values."""
        for key, value in updates.items():
            if hasattr(self.config, key):
                setattr(self.config, key, value)

    def save_config(self, path: Optional[Path] = None):
        """Save current configuration to YAML file."""
        save_path = path or self.config_path
        save_path.parent.mkdir(parents=True, exist_ok=True)

        config_dict = {
            'agent': self.config.agent.__dict__,
            'llm': self.config.llm.__dict__,
            'features': self.config.features.__dict__,
            'tasks': self.config.tasks.__dict__,
            'team': self.config.team.__dict__,
            'compression': self.config.compression.__dict__,
            'background': self.config.background.__dict__,
            'skills': self.config.skills.__dict__,
            'logging': self.config.logging.__dict__
        }

        with open(save_path, 'w', encoding='utf-8') as f:
            yaml.dump(config_dict, f, default_flow_style=False, indent=2)