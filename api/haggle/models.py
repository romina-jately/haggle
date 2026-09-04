"""Wire types. Nothing here computes anything."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

Style = Literal["firm", "balanced", "eager"]


class ListingIn(BaseModel):
    name: str = Field(min_length=1, max_length=140)
    description: str = Field(default="", max_length=2000)
    image_url: str | None = None
    price: float | None = None


class ListingOut(BaseModel):
    id: str
    name: str
    description: str
    image_url: str | None
    price: float
    currency: str = "USD"
    size: str | None = None
    condition: str | None = None
    era: str | None = None
    material: str | None = None
    tags: list[str] = []
    retail_id: str  # SKU for the Meta catalog
    catalog_synced: bool = False


class PolicyIn(BaseModel):
    """Everything the seller controls. Never leaves the server."""

    list_price: float
    floor: float
    style: Style = "balanced"
    deadline_rounds: int = 10
    discount: float | None = None
    opening_concession: float = 0.0
    weights: dict[str, float] = {"time": 0.40, "behaviour": 0.25, "scarcity": 0.10, "belief": 0.25}

    @field_validator("floor")
    @classmethod
    def floor_under_list(cls, v, info):
        lp = info.data.get("list_price")
        if lp is not None and v >= lp:
            raise ValueError("floor must be below list_price")
        return v


class BuyerTurn(BaseModel):
    text: str = Field(min_length=1, max_length=1200)


class AgentTurn(BaseModel):
    """What the buyer sees, plus what the seller sees.

    `seller_view` is stripped before this is rendered to a buyer. It exists
    so the seller's dashboard can show the reasoning without a second call.
    """

    text: str
    round: int
    status: Literal["open", "closed", "walked"]
    quoted_price: float | None = None
    action: Literal["counter", "accept", "answer", "walk"]
    seller_view: dict | None = None


class BuyerMessage(BaseModel):
    """A buyer's turn over the web transport. `buyer` is the web handle; the
    listing is the item they opened the chat from."""

    buyer: str = Field(min_length=1, max_length=80)
    listing_id: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=1200)


class BuyerReply(BaseModel):
    """What the buyer transport returns. Deliberately has no `seller_view`
    field at all: the rationale and belief state cannot leak through a type
    that cannot hold them. See CLAUDE.md — the belief never reaches a buyer."""

    text: str
    round: int
    status: Literal["open", "closed", "walked"]
    quoted_price: float | None = None
    action: Literal["counter", "accept", "answer", "walk"]
