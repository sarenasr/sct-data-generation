"""OpenAI client with multi-key support."""

import re
from typing import Any, Optional, Type

from pydantic import BaseModel

from openai import OpenAI

from ...config import settings
from ...logging import get_logger
from ..key_manager import APIKeyManager, AllKeysExhaustedException

logger = get_logger(__name__)

# Global key manager instance
_key_manager: Optional[APIKeyManager] = None


def get_key_manager() -> APIKeyManager:
    """Get or create the global API key manager for OpenAI."""
    global _key_manager
    if _key_manager is None:
        keys = settings.openai_keys_list
        if not keys:
            raise ValueError(
                "No OpenAI API keys provided. Set OPENAI_API_KEYS or OPENAI_API_KEY environment variable."
            )
        _key_manager = APIKeyManager(keys, provider="openai")
    return _key_manager


def reset_key_manager():
    """Reset the global key manager (useful for testing)."""
    global _key_manager
    _key_manager = None


class OpenAIClient:
    """
    Client for OpenAI Responses API with multi-key support.
    """

    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize OpenAI client.
        
        Args:
            api_key: Optional specific API key to use. If not provided,
                    uses the key manager for automatic rotation.
        """
        self.key_manager = get_key_manager()
        self._specific_key = api_key
        self._client: Optional[OpenAI] = None
        self._current_key: Optional[str] = None

        logger.info("OpenAI client initialized with key manager")

    def _get_client(self) -> OpenAI:
        """Get or create OpenAI client with current key."""
        if self._specific_key:
            key = self._specific_key
        else:
            key = self.key_manager.get_current_key()
        
        # Create new client if key changed
        if self._client is None or self._current_key != key:
            self._client = OpenAI(api_key=key)
            self._current_key = key
        
        return self._client

    def _handle_api_error(self, error: Exception, key: str) -> bool:
        """
        Handle API errors and determine if retry is possible.
        
        Returns:
            True if should retry with different key, False otherwise.
        """
        error_str = str(error).lower()
        
        # Check for rate limit errors
        if "429" in str(error) or "rate limit" in error_str:
            # Try to extract retry-after from error
            retry_after = self._extract_retry_after(str(error))
            self.key_manager.mark_rate_limited(key, retry_after)
            return True
        
        # Check for quota exceeded
        if "quota" in error_str or "exceeded" in error_str:
            self.key_manager.mark_exhausted(key, str(error))
            return True
        
        # Check for authentication errors
        if "401" in str(error) or "invalid" in error_str and "key" in error_str:
            self.key_manager.mark_exhausted(key, "Invalid API key")
            return True
        
        # General error - mark and potentially retry
        self.key_manager.mark_error(key, str(error))
        return False

    def _extract_retry_after(self, error_message: str) -> Optional[float]:
        """Extract retry-after time from error message."""
        # Try to find a number of seconds in the error message
        match = re.search(r'retry after (\d+)', error_message.lower())
        if match:
            return float(match.group(1))
        
        match = re.search(r'(\d+)\s*seconds?', error_message.lower())
        if match:
            return float(match.group(1))
        
        return None

    def generate_simple(
        self,
        input_text: str,
        model: str,
        instructions: str,
    ) -> str:
        """
        Simple text generation with automatic key rotation.

        Args:
            input_text: Input prompt text.
            model: Model to use for generation.
            instructions: Instructions for the model.

        Returns:
            Generated text as a string.
            
        Raises:
            AllKeysExhaustedException: If all API keys are exhausted.
        """
        max_attempts = len(self.key_manager.keys) * 2  # Allow retries across all keys
        
        for attempt in range(max_attempts):
            try:
                client = self._get_client()
                key = self._current_key
                
                params = {
                    "model": model,
                    "input": input_text,
                    "instructions": instructions,
                }

                logger.info(f"Generating text with model: {model}")
                logger.debug(f"Request params: {params}")

                response = client.responses.create(**params)
                output_text = getattr(response, "output_text", "")

                # Mark success
                self.key_manager.mark_success(key)
                
                logger.info(f"Generation successful. Output length: {len(output_text)}")

                return output_text

            except AllKeysExhaustedException:
                raise
            except Exception as e:
                should_retry = self._handle_api_error(e, self._current_key)
                
                if should_retry and self.key_manager.has_available_keys():
                    logger.info(f"Retrying with different key (attempt {attempt + 1}/{max_attempts})")
                    self._client = None  # Force new client creation
                    continue
                elif not self.key_manager.has_available_keys():
                    raise AllKeysExhaustedException(
                        f"All API keys exhausted. Last error: {e}"
                    )
                else:
                    logger.error(f"Error generating text: {e}")
                    raise

        raise AllKeysExhaustedException("Max retry attempts reached")

    def parse_simple(
        self,
        input_text: str,
        model_class: Type[BaseModel],
        model: str,
        instructions: str,
    ) -> Any:
        """
        Structured generation with JSON Schema and automatic key rotation.

        Args:
            input_text: Input prompt text.
            model_class: Pydantic model class defining the output schema.
            model: Model to use for generation.
            instructions: Instructions for the model.

        Returns:
            Parsed Pydantic model instance.

        Raises:
            ValueError: If model refused the request.
            AllKeysExhaustedException: If all API keys are exhausted.
        """
        max_attempts = len(self.key_manager.keys) * 2  # Allow retries across all keys
        
        for attempt in range(max_attempts):
            try:
                client = self._get_client()
                key = self._current_key
                
                params = {
                    "model": model,
                    "input": input_text,
                    "text_format": model_class,
                    "instructions": instructions,
                }

                logger.info(f"Generating structured output with model: {model}")
                logger.debug(f"Request params: {params}")

                response = client.responses.parse(**params)

                # Check for refusal
                refusal = None
                for item in response.output:
                    for content in item.content:
                        if content.type == "refusal":
                            refusal = getattr(content, "refusal", None)
                            break

                if refusal:
                    logger.warning(f"Model refused request: {refusal}")
                    raise ValueError(f"Model refused request: {refusal}")

                output_parsed = getattr(response, "output_parsed", None)

                # Mark success
                self.key_manager.mark_success(key)
                
                logger.info("Structured generation successful")

                return output_parsed

            except ValueError:
                # Re-raise refusals without key rotation
                raise
            except AllKeysExhaustedException:
                raise
            except Exception as e:
                should_retry = self._handle_api_error(e, self._current_key)
                
                if should_retry and self.key_manager.has_available_keys():
                    logger.info(f"Retrying with different key (attempt {attempt + 1}/{max_attempts})")
                    self._client = None  # Force new client creation
                    continue
                elif not self.key_manager.has_available_keys():
                    raise AllKeysExhaustedException(
                        f"All API keys exhausted. Last error: {e}"
                    )
                else:
                    logger.error(f"Error generating structured output: {e}")
                    raise

        raise AllKeysExhaustedException("Max retry attempts reached")
