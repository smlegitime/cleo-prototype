from typing import Literal, Optional
from pydantic import BaseModel, Field, ValidationError

class Locale(BaseModel):
    lang: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None


class LabelValueDefinition(BaseModel):
    identifier: str = Field(
        ...,
        pattern=r'^[a-z]+(_[a-z]+)*$',
        description="snake_case identifier derived from the label's purpose",
    )
    blurs: Literal['content', 'media', 'none'] = 'none'
    severity: Literal['alert', 'inform', 'none'] = 'inform'
    default_setting: Literal['hide', 'warn', 'ignore'] = 'ignore'
    locales: Optional[list[Locale]] = None


class LabelerDeclaration(BaseModel):
    display_name: Optional[str] = None
    description: Optional[str] = None
    labels: Optional[list[LabelValueDefinition]] = None


def validate_labeler_config(config: dict) -> dict:
    if not isinstance(config, dict):
        return {"is_valid": False, "errors": ["Expected a dict, got %s" % type(config).__name__]}

    try:
        LabelerDeclaration.model_validate(config)
        return {"is_valid": True, "errors": []}
    except ValidationError as e:
        errors = []
        for err in e.errors():
            loc = ".".join(str(p) for p in err["loc"])
            msg = err["msg"]
            errors.append(f"{loc}: {msg}")
        return {"is_valid": False, "errors": errors}
