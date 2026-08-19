"""Abstract base class for all ArchivesSpace → Neo4j extractors."""

import logging
from abc import ABC, abstractmethod
from typing import Any

from asapextractor.aspace_client import ASpaceClient
from asapextractor.neo4j_client import Neo4jClient

logger = logging.getLogger(__name__)


class BaseExtractor(ABC):
    """ETL base: extract from ArchivesSpace, transform, load into Neo4j.

    Subclasses implement extract(), transform(), and load().
    Call run() to execute the full pipeline.
    """

    def __init__(self, aspace: ASpaceClient, neo4j: Neo4jClient) -> None:
        self.aspace = aspace
        self.neo4j = neo4j

    @abstractmethod
    def extract(self) -> list[Any]:
        """Fetch raw records from ArchivesSpace."""

    @abstractmethod
    def transform(self, raw: list[Any]) -> list[dict[str, Any]]:
        """Map raw API records to the dict shape expected by load()."""

    @abstractmethod
    def load(self, records: list[dict[str, Any]]) -> None:
        """Write transformed records into Neo4j."""

    def run(self) -> None:
        """Execute extract → transform → load and log a summary."""
        logger.info("[%s] Starting extraction.", self.__class__.__name__)
        raw = self.extract()
        logger.info("[%s] Extracted %d raw records.", self.__class__.__name__, len(raw))

        records = self.transform(raw)
        logger.info(
            "[%s] Transformed %d records (skipped %d).",
            self.__class__.__name__,
            len(records),
            len(raw) - len(records),
        )

        self.load(records)
        logger.info("[%s] Load complete.", self.__class__.__name__)
