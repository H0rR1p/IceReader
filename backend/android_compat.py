from pydantic import BaseModel as PydanticBaseModel, Field, HttpUrl
class LegacyBaseModel(PydanticBaseModel):
    def model_dump(self, **kwargs):
        kwargs.pop("mode", None)
        return self.dict(**kwargs)
    def model_copy(self, **kwargs):
        return self.copy(**kwargs)
    @classmethod
    def model_validate(cls, value):
        return cls.parse_obj(value)
    @classmethod
    def model_validate_json(cls, value):
        return cls.parse_raw(value)

# Android ships pure Python Pydantic 1; desktop development can still use v2.
BaseModel = PydanticBaseModel if hasattr(PydanticBaseModel, 'model_dump') else LegacyBaseModel
