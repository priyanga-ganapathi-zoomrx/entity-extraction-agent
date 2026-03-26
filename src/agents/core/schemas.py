"""Base schemas for entity extraction agents.

This module provides common base classes for activity input schemas.
"""

from dataclasses import dataclass


@dataclass
class BaseActivityInput:
    """Base schema for all activity inputs with common transaction context.

    All entity-specific input schemas should inherit from this base class
    to ensure consistent transaction tracking across congress_id and batch_id.

    Attributes:
        abstract_id: Unique identifier for the abstract
        abstract_title: Title of the abstract
        congress_id: Identifier for the congress (defaults to 0)
        batch_id: Identifier for the batch (defaults to 0)
    """

    abstract_id: int
    abstract_title: str
    congress_id: int = 0
    batch_id: int = 0
