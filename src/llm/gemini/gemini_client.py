"""Gemini client with multi-key support."""

import copy
import re
from typing import Any, Optional, Type

from google import genai
from google.genai import types
from pydantic import BaseModel

from ...config import settings
from ...logging import get_logger
from ..key_manager import APIKeyManager, AllKeysExhaustedException

logger = get_logger(__name__)

# Global key manager instance
_key_manager: Optional[APIKeyManager] = None


def get_key_manager() -> APIKeyManager:
    """Get or create the global API key manager for Gemini."""
    global _key_manager
    if _key_manager is None:
        keys = settings.gemini_keys_list
        if not keys:
            raise ValueError(
                "No Gemini API keys provided. Set GEMINI_API_KEYS or GEMINI_API_KEY environment variable."
            )
        _key_manager = APIKeyManager(keys, provider="gemini")
    return _key_manager


def reset_key_manager():
    """Reset the global key manager (useful for testing)."""
    global _key_manager
    _key_manager = None


def _resolve_refs(schema_dict: dict, definitions: dict) -> dict:
    """
    Recursively resolve $ref references in a JSON schema.
    
    Args:
        schema_dict: Schema dictionary that may contain $ref.
        definitions: Dictionary of definitions to resolve references from.
    
    Returns:
        Schema with all $ref resolved.
    """
    if isinstance(schema_dict, dict):
        # If this is a $ref, resolve it
        if "$ref" in schema_dict:
            ref_path = schema_dict["$ref"]
            # Extract the definition name (e.g., "#/$defs/SCTQuestion" -> "SCTQuestion")
            if ref_path.startswith("#/$defs/"):
                def_name = ref_path.split("/")[-1]
                if def_name in definitions:
                    # Return a resolved copy of the definition
                    resolved = definitions[def_name].copy()
                    return _resolve_refs(resolved, definitions)
            return schema_dict
        
        # Recursively resolve in nested dictionaries
        result = {}
        for key, value in schema_dict.items():
            if isinstance(value, dict):
                result[key] = _resolve_refs(value, definitions)
            elif isinstance(value, list):
                result[key] = [
                    _resolve_refs(item, definitions) if isinstance(item, dict) else item
                    for item in value
                ]
            else:
                result[key] = value
        return result
    
    return schema_dict


def _clean_schema_for_gemini(schema_dict: dict) -> dict:
    """
    Clean and prepare a JSON schema for Gemini API.
    
    This function:
    1. Resolves all $ref references by inlining definitions
    2. Removes fields not supported by Gemini
    
    Args:
        schema_dict: Dictionary representation of a JSON schema.

    Returns:
        Cleaned schema dictionary ready for Gemini.
    """
    # Make a deep copy to avoid modifying the original
    schema_copy = copy.deepcopy(schema_dict)
    
    # Extract $defs if present
    definitions = schema_copy.pop("$defs", {})
    
    # Resolve all $ref references
    if definitions:
        schema_copy = _resolve_refs(schema_copy, definitions)
    
    # Now clean unsupported fields recursively
    def clean_fields(obj):
        if isinstance(obj, dict):
            # Remove fields not supported by Gemini
            obj.pop("examples", None)
            obj.pop("example", None)
            obj.pop("title", None)
            obj.pop("description", None)
            obj.pop("$defs", None)
            
            # Recursively clean nested objects
            for key, value in obj.items():
                if isinstance(value, dict):
                    obj[key] = clean_fields(value)
                elif isinstance(value, list):
                    obj[key] = [
                        clean_fields(item) if isinstance(item, dict) else item
                        for item in value
                    ]
        return obj
    
    return clean_fields(schema_copy)


class GeminiClient:
    """
    Client for Google Gemini API with multi-key support.
    """

    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize Gemini client.
        
        Args:
            api_key: Optional specific API key to use. If not provided,
                    uses the key manager for automatic rotation.
        """
        self.key_manager = get_key_manager()
        self._specific_key = api_key
        self._client: Optional[genai.Client] = None
        self._current_key: Optional[str] = None

        logger.info("Gemini client initialized with key manager")

    def _get_client(self) -> genai.Client:
        """Get or create Gemini client with current key."""
        if self._specific_key:
            key = self._specific_key
        else:
            key = self.key_manager.get_current_key()
        
        # Create new client if key changed
        if self._client is None or self._current_key != key:
            self._client = genai.Client(api_key=key)
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
        if "429" in str(error) or "rate limit" in error_str or "resource exhausted" in error_str:
            retry_after = self._extract_retry_after(str(error))
            self.key_manager.mark_rate_limited(key, retry_after)
            return True
        
        # Check for quota exceeded
        if "quota" in error_str or "exceeded" in error_str:
            self.key_manager.mark_exhausted(key, str(error))
            return True
        
        # Check for authentication errors
        if "401" in str(error) or "403" in str(error) or "invalid" in error_str and "key" in error_str:
            self.key_manager.mark_exhausted(key, "Invalid API key")
            return True
        
        # General error - mark and potentially retry
        self.key_manager.mark_error(key, str(error))
        return False

    def _extract_retry_after(self, error_message: str) -> Optional[float]:
        """Extract retry-after time from error message."""
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
            instructions: System instructions for the model.

        Returns:
            Generated text as a string.
            
        Raises:
            AllKeysExhaustedException: If all API keys are exhausted.
        """
        max_attempts = len(self.key_manager.keys) * 2
        
        for attempt in range(max_attempts):
            try:
                client = self._get_client()
                key = self._current_key
                
                config = types.GenerateContentConfig(
                    system_instruction=instructions,
                )

                logger.info(f"Generating text with model: {model}")
                logger.debug(f"Input text length: {len(input_text)}")

                response = client.models.generate_content(
                    model=model,
                    contents=input_text,
                    config=config,
                )

                output_text = response.text

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
                    self._client = None
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
            instructions: System instructions for the model.

        Returns:
            Parsed Pydantic model instance.

        Raises:
            ValueError: If model refused the request or parsing failed.
            AllKeysExhaustedException: If all API keys are exhausted.
        """
        max_attempts = len(self.key_manager.keys) * 2
        
        for attempt in range(max_attempts):
            try:
                client = self._get_client()
                key = self._current_key
                
                # Get the JSON schema from Pydantic model and clean it for Gemini
                json_schema = model_class.model_json_schema()
                cleaned_schema = _clean_schema_for_gemini(json_schema)

                config = types.GenerateContentConfig(
                    system_instruction=instructions,
                    response_mime_type="application/json",
                    response_schema=cleaned_schema,
                )

                logger.info(f"Generating structured output with model: {model}")
                logger.debug(f"Input text length: {len(input_text)}")
                logger.debug(f"Output schema: {model_class.__name__}")

                response = client.models.generate_content(
                    model=model,
                    contents=input_text,
                    config=config,
                )

                # Get the text response and parse it manually with Pydantic
                response_text = response.text

                # Parse the JSON response with the Pydantic model
                output_parsed = model_class.model_validate_json(response_text)

                # Mark success
                self.key_manager.mark_success(key)
                
                logger.info("Structured generation successful")

                return output_parsed

            except AllKeysExhaustedException:
                raise
            except Exception as e:
                should_retry = self._handle_api_error(e, self._current_key)
                
                if should_retry and self.key_manager.has_available_keys():
                    logger.info(f"Retrying with different key (attempt {attempt + 1}/{max_attempts})")
                    self._client = None
                    continue
                elif not self.key_manager.has_available_keys():
                    raise AllKeysExhaustedException(
                        f"All API keys exhausted. Last error: {e}"
                    )
                else:
                    logger.error(f"Error generating structured output: {e}")
                    raise

        raise AllKeysExhaustedException("Max retry attempts reached")
