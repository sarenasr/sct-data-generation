"""Configuration for SCT data generation."""

from typing import List, Optional

from pydantic_settings import BaseSettings


class DiseaseConfig:
    """Configuration for a specific disease."""
    
    def __init__(self, name: str, display_name: str, guidelines: List[str], questions_per_guideline: int = 20):
        self.name = name
        self.display_name = display_name
        self.guidelines = guidelines
        self.questions_per_guideline = questions_per_guideline
        self.total_questions = questions_per_guideline * len(guidelines)


# Disease configurations
# Guideline folder mappings
GUIDELINE_FOLDERS = {
    "hepatocellular_carcinoma": "hepatocellular carcinoma",
    "icca": "iCCA",
    "intrahepatic_cholangiocarcinoma": "iCCA",  # Alias for full name
}

HEPATOCELLULAR_CARCINOMA = DiseaseConfig(
    name="hepatocellular_carcinoma",
    display_name="Hepatocellular Carcinoma",
    guidelines=[
        "AASLD 2023",   # American Association for Study of Liver Diseases
        "AASLD 2018",   # American Association for Study of Liver Diseases (older)
        "EASL",         # European Association for Study of the Liver
        "BSG",          # British Society of Gastroenterology
        "ESMO",         # European Society for Medical Oncology
    ],
    questions_per_guideline=20
)

INTRAHEPATIC_CHOLANGIOCARCINOMA = DiseaseConfig(
    name="intrahepatic_cholangiocarcinoma",
    display_name="Intrahepatic Cholangiocarcinoma",
    guidelines=[
        "ESMO",       # European Society for Medical Oncology
        "ASCO",       # American Society of Clinical Oncology
        "AASLD",      # American Association for Study of Liver Diseases
        "BSG",        # British Society of Gastroenterology
        "EASL-ILCA",  # European Association for Study of the Liver - International Liver Cancer Association
    ],
    questions_per_guideline=20
)

# All available diseases
AVAILABLE_DISEASES = {
    "hepatocellular_carcinoma": HEPATOCELLULAR_CARCINOMA,
    "icca": INTRAHEPATIC_CHOLANGIOCARCINOMA,
}


class Settings(BaseSettings):
    """Application settings."""

    # LLM Provider Configuration
    llm_provider: str = "openai"  # "openai" or "gemini"

    # OpenAI Configuration - supports multiple comma-separated keys
    openai_api_keys: str = ""  # Comma-separated list of API keys
    
    # Gemini Configuration - supports multiple comma-separated keys
    gemini_api_keys: str = ""  # Comma-separated list of API keys
    
    # Legacy single key support (for backwards compatibility)
    openai_api_key: str = ""
    gemini_api_key: str = ""

    # Generation Configuration
    num_scts_to_generate: int = 100  # Total questions per disease
    questions_per_guideline: int = 20  # Questions per guideline
    model: str = "gpt-4o"

    # Disease selection (comma-separated)
    diseases: str = "hepatocellular_carcinoma,icca"

    # Logging
    log_level: str = "INFO"
    
    # Progress tracking
    progress_file: str = "data/progress.json"

    @property
    def openai_keys_list(self) -> List[str]:
        """Parse OpenAI API keys into a list."""
        keys = []
        # Add keys from comma-separated list
        if self.openai_api_keys:
            keys.extend([k.strip() for k in self.openai_api_keys.split(",") if k.strip()])
        # Add legacy single key if set and not already included
        if self.openai_api_key and self.openai_api_key not in keys:
            keys.append(self.openai_api_key)
        return keys

    @property
    def gemini_keys_list(self) -> List[str]:
        """Parse Gemini API keys into a list."""
        keys = []
        # Add keys from comma-separated list
        if self.gemini_api_keys:
            keys.extend([k.strip() for k in self.gemini_api_keys.split(",") if k.strip()])
        # Add legacy single key if set and not already included
        if self.gemini_api_key and self.gemini_api_key not in keys:
            keys.append(self.gemini_api_key)
        return keys

    @property
    def disease_list(self) -> List[str]:
        """Parse disease selection into a list."""
        return [d.strip() for d in self.diseases.split(",") if d.strip()]

    def get_disease_configs(self) -> List[DiseaseConfig]:
        """Get disease configurations for selected diseases."""
        configs = []
        for disease_name in self.disease_list:
            if disease_name in AVAILABLE_DISEASES:
                configs.append(AVAILABLE_DISEASES[disease_name])
        return configs

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


# Global settings instance
settings = Settings()
