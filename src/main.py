"""
Main entry point.

This application generates SCT (Script Concordance Test) items for liver cancer.
Diseases supported: Hepatocellular Carcinoma (HCC) and Intrahepatic Cholangiocarcinoma (iCCA).
Each disease has 5 guidelines, generating 20 questions per guideline (100 questions per disease).
Questions are generated using 1 API query per SCT item (which contains 3 components: diagnosis, management, followup).
The system supports multiple API keys with automatic rotation for rate limit handling.
"""

import sys
from pathlib import Path
from typing import Dict, List

from .config import settings, AVAILABLE_DISEASES
from .generator import export_generated_items_to_csv
from .generator.sct_generator import run_generation
from .generator.progress import ProgressTracker
from .llm.key_manager import AllKeysExhaustedException
from .logging import get_logger, setup_logging
from .schemas import SCTItem

logger = get_logger(__name__)


def generate_scts(
    model: str, provider: str, diseases: List[str], resume: bool = True
) -> Dict[str, List[SCTItem]]:
    """
    Generate SCT items for specified diseases.

    Each disease has 5 guidelines with 20 questions per guideline.
    Total: 100 questions per disease, 200 questions total.

    Args:
        model: LLM model to use.
        provider: LLM provider to use ("openai" or "gemini").
        diseases: List of disease identifiers.
        resume: Whether to resume from previous progress.

    Returns:
        Dictionary mapping disease names to lists of generated SCTItem objects.
    """
    # Calculate expected totals
    total_diseases = len(diseases)
    questions_per_guideline = settings.questions_per_guideline
    
    # Get total guidelines across all diseases
    total_guidelines = sum(
        len(AVAILABLE_DISEASES[d].guidelines) 
        for d in diseases 
        if d in AVAILABLE_DISEASES
    )
    total_expected = total_guidelines * questions_per_guideline

    logger.info("=" * 70)
    logger.info("STARTING SCT GENERATION")
    logger.info("=" * 70)
    logger.info(f"Provider: {provider.upper()}")
    logger.info(f"Model: {model}")
    logger.info(f"Diseases: {', '.join(diseases)}")
    logger.info(f"Questions per guideline: {questions_per_guideline}")
    logger.info(f"Total questions target: {total_expected}")
    logger.info("=" * 70)

    try:
        results = run_generation(
            model=model,
            provider=provider,
            diseases=diseases,
            resume=resume,
        )

        # Calculate statistics
        total_generated = sum(len(items) for items in results.values())
        
        logger.info("=" * 70)
        logger.info("GENERATION COMPLETE")
        logger.info("=" * 70)
        logger.info(f"Total generated: {total_generated}/{total_expected}")
        
        for disease, items in results.items():
            if disease in AVAILABLE_DISEASES:
                expected = len(AVAILABLE_DISEASES[disease].guidelines) * questions_per_guideline
                logger.info(f"  {disease}: {len(items)}/{expected}")

        return results

    except AllKeysExhaustedException as e:
        logger.error(f"All API keys exhausted: {e}")
        logger.error("Progress has been saved. You can resume later with new API keys.")
        raise

    except Exception as e:
        logger.error(f"Error during generation: {e}")
        raise


def main() -> int:
    """
    Main entry point for the application.

    Returns:
        Exit code (0 for success, 1 for failure, 2 for partial completion).
    """
    # Setup logging
    setup_logging(level=settings.log_level)

    logger.info("=" * 70)
    logger.info("SCT DATA GENERATION APPLICATION")
    logger.info("=" * 70)
    logger.info("Diseases: Hepatocellular Carcinoma (HCC), Intrahepatic Cholangiocarcinoma (iCCA)")
    logger.info("=" * 70)

    # Validate LLM provider
    provider = settings.llm_provider.lower()
    if provider not in ["openai", "gemini"]:
        logger.error(f"ERROR: Invalid LLM_PROVIDER: {provider}")
        logger.error("Must be 'openai' or 'gemini'")
        return 1

    # Validate API keys based on provider
    if provider == "openai":
        keys = settings.openai_keys_list
        if not keys:
            logger.error("ERROR: No OpenAI API keys configured")
            logger.error("Please set OPENAI_API_KEYS or OPENAI_API_KEY in .env file")
            return 1
        logger.info(f"OpenAI API keys configured: {len(keys)}")
    elif provider == "gemini":
        keys = settings.gemini_keys_list
        if not keys:
            logger.error("ERROR: No Gemini API keys configured")
            logger.error("Please set GEMINI_API_KEYS or GEMINI_API_KEY in .env file")
            return 1
        logger.info(f"Gemini API keys configured: {len(keys)}")

    # Validate diseases
    diseases = settings.disease_list
    valid_diseases = [d for d in diseases if d in AVAILABLE_DISEASES]
    
    if not valid_diseases:
        logger.error("ERROR: No valid diseases configured")
        logger.error(f"Available diseases: {list(AVAILABLE_DISEASES.keys())}")
        return 1

    # Display configuration
    logger.info("\nConfiguration:")
    logger.info(f"  LLM Provider: {provider.upper()}")
    logger.info(f"  Model: {settings.model}")
    logger.info(f"  Diseases: {', '.join(valid_diseases)}")
    logger.info(f"  Questions per guideline: {settings.questions_per_guideline}")
    
    for disease in valid_diseases:
        config = AVAILABLE_DISEASES[disease]
        logger.info(f"\n  {config.display_name}:")
        logger.info(f"    Guidelines: {len(config.guidelines)}")
        for guideline in config.guidelines:
            logger.info(f"      - {guideline}")
        logger.info(f"    Questions: {config.total_questions}")
    
    total_questions = sum(
        AVAILABLE_DISEASES[d].total_questions 
        for d in valid_diseases
    )
    logger.info(f"\n  TOTAL QUESTIONS: {total_questions}")
    logger.info("")

    try:
        # Generate SCTs
        results = generate_scts(
            model=settings.model,
            provider=provider,
            diseases=valid_diseases,
            resume=True,  # Always try to resume from previous progress
        )

        # Count total items
        total_items = sum(len(items) for items in results.values())
        
        if total_items == 0:
            logger.error("\nNo items were generated successfully")
            return 1

        # Export all generated items to CSV
        logger.info("\n" + "=" * 70)
        logger.info("EXPORTING TO CSV")
        logger.info("=" * 70)

        try:
            validated_dir = Path("data/validated")
            output_dir = Path("data/exports")

            csv_path = export_generated_items_to_csv(
                validated_dir=validated_dir, output_dir=output_dir
            )

            if csv_path:
                logger.info(f"✓ CSV export successful: {csv_path}")
            else:
                logger.warning("CSV export skipped (no items found in validated folder)")

        except Exception as e:
            logger.error(f"Failed to export CSV: {e}")
            logger.warning("Continuing despite CSV export failure...")

        logger.info("\n" + "=" * 70)
        logger.info("APPLICATION COMPLETED SUCCESSFULLY")
        logger.info("=" * 70)
        return 0

    except AllKeysExhaustedException:
        logger.warning("\n" + "=" * 70)
        logger.warning("APPLICATION STOPPED - API KEYS EXHAUSTED")
        logger.warning("=" * 70)
        logger.warning("Progress has been saved. To resume:")
        logger.warning("  1. Add new API keys to your .env file")
        logger.warning("  2. Run the application again - it will resume from where it stopped")
        
        # Try to export what we have
        try:
            validated_dir = Path("data/validated")
            output_dir = Path("data/exports")
            
            csv_path = export_generated_items_to_csv(
                validated_dir=validated_dir, 
                output_dir=output_dir,
                filename="sct_items_partial.csv"
            )
            if csv_path:
                logger.info(f"✓ Partial results exported to: {csv_path}")
        except Exception as e:
            logger.error(f"Failed to export partial results: {e}")
        
        return 2  # Partial completion

    except KeyboardInterrupt:
        logger.warning("\n\nGeneration interrupted by user")
        logger.warning("Progress has been saved. Run again to resume.")
        return 2  # Partial completion

    except Exception as e:
        logger.error(f"\n\nFATAL ERROR: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
