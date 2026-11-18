from pydantic import BaseModel,ConfigDict,Field


class TeardownPlanInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    run_id:int
    name:str=Field(min_length=2,max_length=160)
    seed:int=Field(default=42,ge=0,le=2_147_483_647)
    per_stratum_sample:int=Field(default=0,ge=0,le=100000)
    sample_sizes:dict[str,int]=Field(default_factory=dict)


class TeardownReceiptInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    reference:str=Field(min_length=1,max_length=120)
    confirmed_label:str=Field(min_length=1,max_length=600)
    received_at:str|None=None


class TeardownReceiptBatch(BaseModel):
    model_config=ConfigDict(extra='forbid')
    receipts:list[TeardownReceiptInput]=Field(min_length=1,max_length=2000)


class TeardownEditionInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    receipt_cutoff:str|None=None
