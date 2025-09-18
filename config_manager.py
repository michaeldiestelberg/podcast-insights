#!/usr/bin/env python3
"""Configuration management for Podcast Insights."""

import os
import yaml
from pathlib import Path
from typing import Dict, Any, Optional
from dataclasses import dataclass


@dataclass
class ToolPaths:
    """Tool paths configuration."""
    transcribe_cmd: str
    insights_cmd: str


class ConfigManager:
    """Manages configuration with fallback to submodules and auto-detection."""

    def __init__(self, root_dir: Path = None):
        self.root_dir = root_dir or Path.cwd()
        self.config_path = self.root_dir / "config.yaml"
        self.submodules_dir = self.root_dir / "tools"

    def detect_tool_paths(self) -> ToolPaths:
        """Auto-detect tool paths from submodules or config."""
        # First, try to load from config.yaml if it exists
        if self.config_path.exists():
            with open(self.config_path, 'r') as f:
                config = yaml.safe_load(f)
                if config and 'tools' in config:
                    return ToolPaths(
                        transcribe_cmd=config['tools'].get('transcribe_cmd', ''),
                        insights_cmd=config['tools'].get('insights_cmd', '')
                    )

        # Fallback to submodule paths
        transcribe_path = self.submodules_dir / "podcast-transcription" / "transcribe.sh"
        ai_prompt_path = self.submodules_dir / "ai-cli" / "ai-prompt"

        # Check if submodules exist
        if transcribe_path.exists() and ai_prompt_path.exists():
            return ToolPaths(
                transcribe_cmd=f'{transcribe_path} "{{audio}}" -o "{{transcript}}"',
                insights_cmd=f'{ai_prompt_path} --prompt "{{transcript}}" --system-prompt podcast-insights --output-path "{{episode_dir}}" --output-name "{{insights_file}}" --model gpt-5-mini'
            )

        # If neither config nor submodules exist, return empty paths
        return ToolPaths(transcribe_cmd='', insights_cmd='')

    def validate_config(self, config: Dict[str, Any]) -> tuple[bool, str]:
        """Validate configuration and return status with message."""
        errors = []

        # Check required sections
        required_sections = ['storage', 'tools', 'feeds']
        for section in required_sections:
            if section not in config:
                errors.append(f"Missing required section: {section}")

        # Validate tools section
        if 'tools' in config:
            tools = config['tools']
            if not tools.get('transcribe_cmd'):
                errors.append("Missing transcribe_cmd in tools section")
            else:
                # Extract path from command and check if it exists
                cmd_parts = tools['transcribe_cmd'].split()
                if cmd_parts:
                    tool_path = cmd_parts[0].strip('"')
                    if not Path(tool_path).exists():
                        errors.append(f"Transcription tool not found: {tool_path}")

            if not tools.get('insights_cmd'):
                errors.append("Missing insights_cmd in tools section")
            else:
                # Extract path from command and check if it exists
                cmd_parts = tools['insights_cmd'].split()
                if cmd_parts:
                    tool_path = cmd_parts[0].strip('"')
                    if not Path(tool_path).exists():
                        errors.append(f"AI CLI tool not found: {tool_path}")

        # Validate storage section
        if 'storage' in config:
            storage = config['storage']
            if not storage.get('data_dir'):
                errors.append("Missing data_dir in storage section")
            if not storage.get('temp_dir'):
                errors.append("Missing temp_dir in storage section")

        # Check if feeds exist
        if 'feeds' in config and not config['feeds']:
            errors.append("No RSS feeds configured")

        if errors:
            return False, "\n".join(errors)

        return True, "Configuration is valid"

    def load_config(self) -> Optional[Dict[str, Any]]:
        """Load configuration with auto-detection fallback."""
        if not self.config_path.exists():
            # Try to auto-generate a basic config
            tool_paths = self.detect_tool_paths()
            if tool_paths.transcribe_cmd and tool_paths.insights_cmd:
                config = {
                    'storage': {
                        'data_dir': './data',
                        'temp_dir': './data/_tmp'
                    },
                    'runtime': {
                        'max_retries': 3,
                        'retry_backoff_seconds': 5
                    },
                    'tools': {
                        'transcribe_cmd': tool_paths.transcribe_cmd,
                        'insights_cmd': tool_paths.insights_cmd
                    },
                    'feeds': []
                }
                return config
            return None

        with open(self.config_path, 'r') as f:
            return yaml.safe_load(f)

    def save_config(self, config: Dict[str, Any]) -> None:
        """Save configuration to file."""
        with open(self.config_path, 'w') as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)

    def update_model(self, model: str) -> bool:
        """Update the AI model in the configuration."""
        config = self.load_config()
        if not config:
            return False

        if 'tools' in config and 'insights_cmd' in config['tools']:
            cmd = config['tools']['insights_cmd']
            # Replace the model parameter
            import re
            new_cmd = re.sub(r'--model\s+[\w-]+', f'--model {model}', cmd)
            config['tools']['insights_cmd'] = new_cmd
            self.save_config(config)
            return True

        return False

    def get_configured_model(self) -> Optional[str]:
        """Extract the currently configured model from insights_cmd."""
        config = self.load_config()
        if config and 'tools' in config and 'insights_cmd' in config['tools']:
            import re
            match = re.search(r'--model\s+([\w-]+)', config['tools']['insights_cmd'])
            if match:
                return match.group(1)
        return None

    def check_api_keys(self) -> Dict[str, bool]:
        """Check which API keys are configured in ai-cli."""
        env_paths = [
            self.submodules_dir / "ai-cli" / ".env",
            Path.home() / ".ai-cli" / ".env",
        ]

        keys_found = {
            'openai': False,
            'anthropic': False
        }

        for env_path in env_paths:
            if env_path.exists():
                with open(env_path, 'r') as f:
                    content = f.read()
                    if 'OPENAI_API_KEY=' in content:
                        keys_found['openai'] = True
                    if 'ANTHROPIC_API_KEY=' in content:
                        keys_found['anthropic'] = True
                break  # Use first found .env file

        return keys_found