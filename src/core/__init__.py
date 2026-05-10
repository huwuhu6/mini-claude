# Core modules
from .features import FeatureManager, FeatureDefinition, FeatureDependency
from .messaging import MessageBus, Message, MessagePriority
from .teammate_manager import TeammateManager, TeammateConfig
from .background import BackgroundProcessor, BackgroundTask, BackgroundTaskStatus
from .subagent import SubAgent, SubAgentManager, SubAgentResult, SubAgentType
from .console import ConsoleCommandSystem, Command
from .compression import Compressor, CompressedTranscript

__all__ = [
    'FeatureManager', 'FeatureDefinition', 'FeatureDependency',
    'MessageBus', 'Message', 'MessagePriority',
    'TeammateManager', 'TeammateConfig',
    'BackgroundProcessor', 'BackgroundTask', 'BackgroundTaskStatus',
    'SubAgent', 'SubAgentManager', 'SubAgentResult', 'SubAgentType',
    'ConsoleCommandSystem', 'Command',
    'Compressor', 'CompressedTranscript',
]
