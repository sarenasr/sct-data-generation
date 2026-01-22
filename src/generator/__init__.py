"""Generator module."""

from .export import export_generated_items_to_csv, load_all_generated_items
from .progress import GenerationProgress, ProgressTracker, QuestionProgress
from .sct_generator import SCTItemGenerator, run_generation
from .utils import save_sct_item

# Legacy imports for backward compatibility
from .generate_sct import (
    GuidelineType,
    SCTGenerator,
    generate_items_per_guideline,
    generate_multiple_items,
    generate_sct_item,
)

__all__ = [
    # New generation system
    "SCTItemGenerator",
    "run_generation",
    "ProgressTracker",
    "GenerationProgress",
    "QuestionProgress",
    # Export utilities
    "export_generated_items_to_csv",
    "load_all_generated_items",
    "save_sct_item",
    # Legacy (backward compatibility)
    "SCTGenerator",
    "generate_sct_item",
    "generate_multiple_items",
    "generate_items_per_guideline",
    "GuidelineType",
]

