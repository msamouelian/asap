"""ArchivesSpace API client: authentication and HTTP GET helpers."""

import logging
from typing import Any, Generator

import requests
from asnake.aspace import ASpace

from asapextractor import config

logger = logging.getLogger(__name__)


class ASpaceClient:
    """Thin wrapper around the ArchivesSpace REST API.

    Authentication is handled by asnake, which reads credentials from
    ~/.asnake.yml automatically — we never access that file directly.
    All network calls are read-only GET requests.
    """

    def __init__(self) -> None:
        aspace = ASpace()
        self._token: str = aspace.client.authorize()
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Accept": "application/json",
                "X-ArchivesSpace-Session": self._token,
            }
        )
        logger.info("ASpaceClient authenticated successfully.")

    def call_endpoint(self, endpoint: str, params: dict[str, Any] | None = None) -> Any:
        """Submit a GET request to an ArchivesSpace API endpoint.

        Args:
            endpoint: Path relative to the API base URL,
                      e.g. "repositories/11/resources".
            params:   Optional query parameters dict, e.g. {"page": 1}.

        Returns:
            Parsed JSON response body.

        Raises:
            RuntimeError: If the server returns a non-200 status code.
        """
        url = f"{config.ASPACE_BASE_URL}/{endpoint.lstrip('/')}"
        response = self._session.get(url, params=params or {})

        if response.status_code != 200:
            raise RuntimeError(
                f"GET {url} failed [{response.status_code}]: {response.text}"
            )

        return response.json()

    def get_paged(
        self,
        endpoint: str,
        page_size: int = config.ASPACE_PAGE_SIZE,
    ) -> Generator[list[Any], None, None]:
        """Yield successive pages of results from a paginated endpoint.

        Args:
            endpoint:  Path relative to the API base URL.
            page_size: Number of records per page.

        Yields:
            List of records from each page.
        """
        page = 1
        while True:
            logger.debug("Fetching %s page=%d page_size=%d", endpoint, page, page_size)
            data = self.call_endpoint(
                endpoint, params={"page": page, "page_size": page_size}
            )

            results: list[Any] = data.get("results", [])
            yield results

            if data.get("last_page", page) <= page:
                break

            page += 1
