"""
LLM-based extractor — pulls all customers from a Jira ticket description
using Azure OpenAI function calling (structured output).

The description format is free-form, e.g.:
    Anup anuph@vyuhnet.com;
    Swapnil swapnilh@vyuhnet.com;
    vikas sehgal vikas@vyuhnet.com

The function returns one ExtractedDetails per customer found.
lastname is left empty when only a single name word is present.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from openai import AzureOpenAI

logger = logging.getLogger(__name__)

_FUNCTION = {
    "name": "extract_customers",
    "description": (
        "Extract every customer entry from a Jira ticket description. "
        "Each entry contains a name (one or two words) and an email address. "
        "If only one name word is present, set firstname to that word and lastname to an empty string."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "customers": {
                "type": "array",
                "description": "List of all customers found in the description",
                "items": {
                    "type": "object",
                    "properties": {
                        "firstname": {"type": "string", "description": "First name"},
                        "lastname":  {"type": "string", "description": "Last name, empty string if not present"},
                        "email":     {"type": "string", "description": "Email address"},
                    },
                    "required": ["firstname", "lastname", "email"],
                },
            }
        },
        "required": ["customers"],
    },
}


@dataclass(frozen=True)
class ExtractedDetails:
    firstname: str
    lastname: str
    email: str


@dataclass(frozen=True)
class AzureOpenAIConfig:
    api_key: str
    endpoint: str
    deployment: str
    api_version: str


def extract_customer_details(
    description: str, az_cfg: AzureOpenAIConfig
) -> list[ExtractedDetails]:
    """Call Azure OpenAI to extract all customers from the ticket description.

    Returns a list with one ExtractedDetails per customer found.
    Raises ValueError if the description is empty or no customers are extracted.
    """
    if not description or not description.strip():
        raise ValueError("Ticket description is empty — cannot extract customer details.")

    client = AzureOpenAI(
        api_key=az_cfg.api_key,
        azure_endpoint=az_cfg.endpoint,
        api_version=az_cfg.api_version,
    )

    logger.debug("Sending description to Azure OpenAI for extraction (%d chars)", len(description))

    response = client.chat.completions.create(
        model=az_cfg.deployment,
        messages=[
            {
                "role": "user",
                "content": (
                    "Extract all customer entries from this Jira ticket description. "
                    "Each entry has a name (one or two words) followed by an email.\n\n"
                    f"{description}"
                ),
            }
        ],
        tools=[{"type": "function", "function": _FUNCTION}],
        tool_choice={"type": "function", "function": {"name": "extract_customers"}},
        max_tokens=512,
    )

    tool_call = response.choices[0].message.tool_calls[0]
    data = json.loads(tool_call.function.arguments)
    raw_list = data.get("customers", [])

    if not raw_list:
        raise ValueError("LLM returned no customers from the description.")

    results: list[ExtractedDetails] = []
    for i, item in enumerate(raw_list):
        firstname = item.get("firstname", "").strip()
        lastname  = item.get("lastname", "").strip()
        email     = item.get("email", "").strip().lower()

        if not firstname or not email:
            logger.warning("Customer entry %d skipped — missing firstname or email: %s", i + 1, item)
            continue

        results.append(ExtractedDetails(firstname=firstname, lastname=lastname or " ", email=email))
        logger.info("Extracted customer %d — %s %s <%s>", i + 1, firstname, lastname, email)

    if not results:
        raise ValueError("No valid customer entries could be extracted from the description.")

    return results
