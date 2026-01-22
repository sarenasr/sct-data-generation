"""New SCT Item Generator using 1 query per SCT question."""

import json
import time
from pathlib import Path
from typing import Dict, List, Optional

from ..config import settings, DiseaseConfig, AVAILABLE_DISEASES, GUIDELINE_FOLDERS
from ..llm.gemini import GeminiClient
from ..llm.openai import OpenAIClient
from ..llm.key_manager import AllKeysExhaustedException
from ..logging import get_logger
from ..schemas import SCTItem
from ..validators import validate_sct_item
from ..validators.utils import save_validated_sct
from .progress import ProgressTracker, QuestionProgress
from .validator_model import SCTModelValidator

logger = get_logger(__name__)

# Retry configuration
MAX_RETRIES = 5  # Increased to handle multiple API key rotations
RETRY_DELAY_BASE = 3  # Reduced base delay since key rotation handles rate limits
MAX_VALIDATION_RETRIES = 2  # Retries for validation failures


class SCTItemGenerator:
    """
    Generator for SCT items using 1 query per question.
    
    Each SCT item is generated with a single API call using one specific guideline.
    The generator then validates the item using the validator prompt.
    """

    def __init__(self, provider: str = "openai"):
        """
        Initialize SCT generator.

        Args:
            provider: LLM provider to use ("openai" or "gemini").
        """
        self.provider = provider.lower()

        if self.provider == "openai":
            self.client = OpenAIClient()
        elif self.provider == "gemini":
            self.client = GeminiClient()
        else:
            raise ValueError(
                f"Invalid provider: {provider}. Must be 'openai' or 'gemini'"
            )

        # Load XML prompt templates
        prompts_dir = Path(__file__).parent.parent / "prompts"
        
        # Load generation prompt
        self.generation_prompt_path = prompts_dir / "sct_item_prompt.xml"
        if not self.generation_prompt_path.exists():
            raise FileNotFoundError(f"Generation prompt not found: {self.generation_prompt_path}")
        
        with open(self.generation_prompt_path, "r", encoding="utf-8") as f:
            self.generation_prompt_template = f.read()

        # Load validator prompt
        self.validator_prompt_path = prompts_dir / "sct_validator_prompt.xml"
        if not self.validator_prompt_path.exists():
            raise FileNotFoundError(f"Validator prompt not found: {self.validator_prompt_path}")
        
        with open(self.validator_prompt_path, "r", encoding="utf-8") as f:
            self.validator_prompt_template = f.read()

        # Load guidelines directory
        self.guidelines_dir = prompts_dir / "guidelines"
        
        logger.info(f"SCT Item Generator initialized with provider: {self.provider}")

    def _load_guideline_content(self, guideline_name: str, disease: str = None) -> str:
        """
        Load guideline content from file.
        
        Args:
            guideline_name: Name of the guideline to load.
            disease: Disease identifier for folder mapping.
            
        Returns:
            Guideline content as string.
        """
        # Determine the correct folder based on disease
        if disease:
            folder_name = GUIDELINE_FOLDERS.get(disease, disease)
            guideline_path = self.guidelines_dir / folder_name / f"{guideline_name}.xml"
            
            if guideline_path.exists():
                with open(guideline_path, "r", encoding="utf-8") as f:
                    content = f.read()
                    logger.info(f"✓ Loaded guideline: {guideline_path}")
                    return content
            
            # Try without disease folder (fallback)
            logger.debug(f"Guideline not found at {guideline_path}, trying root guidelines folder")
        
        # Try loading from root guidelines directory
        guideline_path = self.guidelines_dir / f"{guideline_name}.xml"
        
        if guideline_path.exists():
            with open(guideline_path, "r", encoding="utf-8") as f:
                content = f.read()
                logger.info(f"✓ Loaded guideline: {guideline_path}")
                return content
        
        # Return placeholder if guideline not found
        logger.warning(f"Guideline file not found: {guideline_path}")
        return f"<guideline name='{guideline_name}'>\n  <!-- Guideline content to be added -->\n</guideline>"

    def _render_generation_prompt(
        self,
        disease: str,
        disease_display_name: str,
        guideline_name: str,
        guideline_content: str,
    ) -> str:
        """
        Render the SCT generation prompt with disease and guideline context.

        Args:
            disease: Disease name.
            disease_display_name: Display name for the disease.
            guideline_name: Guideline identifier.
            guideline_content: Full guideline content.

        Returns:
            Rendered prompt string.
        """
        # Build parameters
        parameters = f"""      <disease>{disease_display_name}</disease>
      <guideline_source>{guideline_name}</guideline_source>
      <domain>Oncology - Liver Cancer</domain>"""

        # Replace placeholder
        rendered = self.generation_prompt_template.replace("{{PARAMETERS}}", parameters)
        
        # Add guideline context
        combined_prompt = f"""<clinical_guideline_context>
The following clinical guideline for {disease_display_name} should inform the generation of realistic, guideline-based scenarios:

{guideline_content}

Use this guideline as reference to ensure clinical accuracy and reflect real-world practice patterns.
Generate an SCT item specifically about {disease_display_name} using recommendations from this guideline.
</clinical_guideline_context>

{rendered}"""

        return combined_prompt

    def _render_validator_prompt(
        self,
        item: SCTItem,
        validator_guideline_name: str,
        validator_guideline_content: str,
    ) -> str:
        """
        Render the validator prompt for an SCT item.

        Args:
            item: SCTItem to validate.
            validator_guideline_name: Name of guideline for validation.
            validator_guideline_content: Content of validator guideline.

        Returns:
            Rendered validator prompt.
        """
        # Build questions XML
        questions_xml = ""
        for question in item.questions:
            options_str = ", ".join(question.options)
            questions_xml += f"""      <question>
        <question_type>{question.question_type}</question_type>
        <hypothesis>{question.hypothesis}</hypothesis>
        <new_information>{question.new_information}</new_information>
        <effect_phrase>{question.effect_phrase}</effect_phrase>
        <options>[{options_str}]</options>
        <author_notes>{question.author_notes}</author_notes>
      </question>
"""

        # Handle optional author_notes
        author_notes_section = ""
        if item.author_notes:
            author_notes_section = f"    <author_notes>{item.author_notes}</author_notes>"

        # Replace placeholders
        prompt = self.validator_prompt_template
        prompt = prompt.replace("{{CREATION_GUIDELINE}}", item.guideline or "unknown")
        prompt = prompt.replace("{{VALIDATOR_GUIDELINE}}", validator_guideline_name)
        prompt = prompt.replace("{{DOMAIN}}", item.domain)
        prompt = prompt.replace("{{VIGNETTE}}", item.vignette)
        prompt = prompt.replace("{{AUTHOR_NOTES_SECTION}}", author_notes_section)
        prompt = prompt.replace("{{QUESTIONS_SECTION}}", questions_xml.strip())
        prompt = prompt.replace("{{VALIDATOR_GUIDELINE_CONTENT}}", validator_guideline_content)

        return prompt

    def generate_single_item(
        self,
        model: str,
        disease: str,
        disease_display_name: str,
        guideline_name: str,
        question_number: int,
    ) -> SCTItem:
        """
        Generate a single SCT item for a specific disease and guideline.

        Args:
            model: LLM model to use.
            disease: Disease identifier.
            disease_display_name: Human-readable disease name.
            guideline_name: Guideline to use for generation.
            question_number: Question number within this guideline.

        Returns:
            Generated SCTItem.

        Raises:
            AllKeysExhaustedException: If all API keys are exhausted.
        """
        logger.info(f"Generating SCT item #{question_number} for {disease} using {guideline_name}")

        # Load guideline content (pass disease for correct folder mapping)
        guideline_content = self._load_guideline_content(guideline_name, disease=disease)

        # Render prompt
        prompt = self._render_generation_prompt(
            disease=disease,
            disease_display_name=disease_display_name,
            guideline_name=guideline_name,
            guideline_content=guideline_content,
        )

        # Generate instructions
        instructions = (
            f"Generate a complete SCT item in English about {disease_display_name} "
            f"following EXACTLY the format and quality criteria provided. "
            f"Use the {guideline_name} clinical guideline as the basis for clinical accuracy. "
            "Ensure all fields meet the quality specifications. "
            "CRITICAL REQUIREMENTS: "
            "1. The 'options' field MUST be exactly ['+2', '+1', '0', '-1', '-2'] - DO NOT modify these fixed scale values. "
            "2. The 'vignette' MUST be 120-240 words (aim for 150-200 words for optimal depth). COUNT CAREFULLY. "
            "3. Each question's 'author_notes' MUST end with the expected scale value in parentheses, e.g., '(+2)', '(-1)', '(0)'. "
            "4. Include an optional 'author_notes' field at the item level explaining the overall clinical context (max 500 characters)."
        )

        # Generate item
        sct_item = self.client.parse_simple(
            input_text=prompt,
            model_class=SCTItem,
            model=model,
            instructions=instructions,
        )

        # Set guideline and domain
        sct_item.guideline = guideline_name
        sct_item.domain = f"{disease_display_name}"

        logger.info(f"✓ SCT item generated for {disease} - {guideline_name}")
        
        return sct_item

    def validate_item(
        self,
        item: SCTItem,
        model: str,
        validator_guideline_name: str,
        disease: str = None,
    ) -> SCTItem:
        """
        Validate an SCT item using the validator prompt.

        Args:
            item: SCTItem to validate.
            model: LLM model to use.
            validator_guideline_name: Guideline to use for validation.
            disease: Disease identifier for folder mapping.

        Returns:
            SCTItem with validator_result populated.

        Raises:
            AllKeysExhaustedException: If all API keys are exhausted.
        """
        logger.info(f"Validating SCT item using {validator_guideline_name} guideline")

        # Load validator guideline content (pass disease for correct folder mapping)
        validator_guideline_content = self._load_guideline_content(validator_guideline_name, disease=disease)

        # Render validator prompt
        prompt = self._render_validator_prompt(
            item=item,
            validator_guideline_name=validator_guideline_name,
            validator_guideline_content=validator_guideline_content,
        )

        # Generate validation instructions
        instructions = (
            f"You are validating an SCT item using the {validator_guideline_name} clinical guideline "
            f"(different from the guideline used for creation: {item.guideline}). "
            "Select the most appropriate response option (+2, +1, 0, -1, or -2) "
            "for each of the 3 questions based on how the new information affects "
            "the hypothesis/plan according to your guideline perspective. "
            "Provide overall validation notes explaining your assessment."
        )

        from ..schemas.sct_schemas import SCTValidatorResult

        # Generate validation
        validator_result = self.client.parse_simple(
            input_text=prompt,
            model_class=SCTValidatorResult,
            model=model,
            instructions=instructions,
        )

        # Attach result to item
        item.validator_result = validator_result
        
        logger.info(f"✓ Validation completed using {validator_guideline_name}")
        
        return item


def run_generation(
    model: str,
    provider: str = "openai",
    diseases: Optional[List[str]] = None,
    resume: bool = True,
) -> Dict[str, List[SCTItem]]:
    """
    Run the full SCT generation process.

    Args:
        model: LLM model to use.
        provider: LLM provider ("openai" or "gemini").
        diseases: List of disease identifiers to generate for. 
                 If None, uses all configured diseases.
        resume: Whether to resume from previous progress.

    Returns:
        Dictionary mapping disease names to lists of generated SCTItems.

    Raises:
        AllKeysExhaustedException: If all API keys are exhausted.
    """
    # Initialize tracker
    tracker = ProgressTracker()
    
    # Get disease configurations
    if diseases is None:
        diseases = settings.disease_list
    
    disease_configs = {}
    guidelines_per_disease = {}
    
    for disease_name in diseases:
        if disease_name in AVAILABLE_DISEASES:
            config = AVAILABLE_DISEASES[disease_name]
            disease_configs[disease_name] = config
            guidelines_per_disease[disease_name] = config.guidelines
        else:
            logger.warning(f"Unknown disease: {disease_name}")
    
    if not disease_configs:
        raise ValueError("No valid diseases configured")
    
    # Try to resume or start new session
    if resume:
        progress = tracker.load_progress()
        # Resume from running, interrupted, or exhausted sessions
        if progress and progress.status in ["running", "interrupted", "exhausted"]:
            logger.info(f"Resuming from previous session (status: {progress.status})...")
            # Reset status to running
            progress.status = "running"
            tracker.save_progress()
        else:
            progress = None
    else:
        progress = None
    
    if progress is None:
        # Start new session
        progress = tracker.start_session(
            diseases=list(disease_configs.keys()),
            guidelines_per_disease=guidelines_per_disease,
            questions_per_guideline=settings.questions_per_guideline,
        )
    
    # Initialize generator
    generator = SCTItemGenerator(provider=provider)
    
    # Get list of pending questions
    pending = tracker.get_pending_questions()
    logger.info(f"Questions to generate: {len(pending)}")
    
    # Generate results storage
    results: Dict[str, List[SCTItem]] = {d: [] for d in disease_configs.keys()}
    
    # Process pending questions
    try:
        for i, question in enumerate(pending):
            # Check for shutdown request
            if ProgressTracker.is_shutdown_requested():
                logger.warning("Shutdown requested. Saving progress...")
                break
            
            disease_config = disease_configs.get(question.disease)
            if not disease_config:
                tracker.mark_failed(question.question_id, f"Unknown disease: {question.disease}")
                continue
            
            logger.info(f"\n[{i+1}/{len(pending)}] Generating: {question.question_id}")
            
            # Retry logic for API errors
            retry_count = 0
            success = False
            
            while retry_count <= MAX_RETRIES and not success:
                try:
                    # Inner retry loop for validation failures
                    validation_retry = 0
                    item = None
                    validation_result = None
                    
                    while validation_retry <= MAX_VALIDATION_RETRIES:
                        # Step 1: Generate SCT item
                        item = generator.generate_single_item(
                            model=model,
                            disease=question.disease,
                            disease_display_name=disease_config.display_name,
                            guideline_name=question.guideline,
                            question_number=question.question_number,
                        )
                        
                        # Step 2: Validate the item
                        validation_result = validate_sct_item(item)
                        
                        if validation_result.is_valid:
                            break  # Validation passed, continue
                        else:
                            validation_retry += 1
                            if validation_retry <= MAX_VALIDATION_RETRIES:
                                logger.warning(f"Validation failed (attempt {validation_retry}/{MAX_VALIDATION_RETRIES}): {validation_result.errors}")
                                logger.info("Regenerating item...")
                            else:
                                logger.warning(f"Validation failed after {MAX_VALIDATION_RETRIES} retries: {validation_result.errors}")
                    
                    # Step 3: Validate with model using a different guideline
                    # Select a different guideline for validation
                    available_guidelines = [g for g in disease_config.guidelines if g != question.guideline]
                    if available_guidelines:
                        validator_guideline = available_guidelines[0]
                        item = generator.validate_item(
                            item=item,
                            model=model,
                            validator_guideline_name=validator_guideline,
                            disease=question.disease,
                        )
                    
                    # Step 4: Save the item (validation_failed folder handled automatically by the function)
                    save_validated_sct(item, validation_result)
                    
                    # Generate file path for tracking
                    file_path = f"data/generated/sct_{question.question_id}.json"
                    
                    # Update progress
                    tracker.mark_generated(question.question_id, file_path)
                    if item.validator_result:
                        tracker.mark_validated(question.question_id)
                    
                    results[question.disease].append(item)
                    success = True
                    
                    logger.info(f"✓ Completed: {question.question_id}")
                    
                except AllKeysExhaustedException as e:
                    logger.error(f"All API keys exhausted: {e}")
                    # Update API key stats before raising
                    if provider == "openai":
                        from ..llm.openai import get_key_manager
                    else:
                        from ..llm.gemini import get_key_manager
                    
                    key_manager = get_key_manager()
                    tracker.set_status("exhausted", key_manager.get_stats())
                    raise
                    
                except Exception as e:
                    retry_count += 1
                    error_str = str(e)
                    
                    # Check if retryable (include more error types for API key rotation)
                    is_retryable = (
                        "503" in error_str or
                        "429" in error_str or
                        "UNAVAILABLE" in error_str or
                        "overloaded" in error_str.lower() or
                        "timeout" in error_str.lower() or
                        "quota" in error_str.lower() or
                        "resource exhausted" in error_str.lower() or
                        "rate limit" in error_str.lower() or
                        "too many requests" in error_str.lower()
                    )
                    
                    if is_retryable and retry_count <= MAX_RETRIES:
                        delay = RETRY_DELAY_BASE * (2 ** (retry_count - 1))
                        logger.warning(f"Retryable error (attempt {retry_count}/{MAX_RETRIES}): {e}")
                        logger.info(f"Waiting {delay} seconds before retry...")
                        time.sleep(delay)
                    else:
                        logger.error(f"Failed to generate {question.question_id}: {e}")
                        tracker.mark_failed(question.question_id, str(e))
                        break
    
    except AllKeysExhaustedException:
        logger.error("Generation stopped: All API keys exhausted")
        tracker.print_summary()
        raise
    
    except KeyboardInterrupt:
        logger.warning("\nGeneration interrupted by user")
        tracker.set_status("interrupted")
    
    finally:
        # Get API key stats
        if provider == "openai":
            from ..llm.openai import get_key_manager
        else:
            from ..llm.gemini import get_key_manager
        
        try:
            key_manager = get_key_manager()
            api_stats = key_manager.get_stats()
        except:
            api_stats = None
        
        # Update final status if not already set
        if tracker.progress and tracker.progress.status == "running":
            if tracker.progress.pending_count == 0:
                tracker.set_status("completed", api_stats)
            else:
                tracker.set_status("interrupted", api_stats)
        
        # Print summary
        tracker.print_summary()
    
    return results
