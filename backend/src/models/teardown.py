from sqlalchemy import Column,Integer,String,Text,Float,JSON,ForeignKey,UniqueConstraint
from ..config.database import Base


class TeardownPlan(Base):
    __tablename__='teardown_plans'
    id=Column(Integer,primary_key=True)
    run_id=Column(Integer,ForeignKey('classifier_runs.id'),nullable=False)
    name=Column(String(160),nullable=False)
    run_name=Column(String(160),nullable=False)
    frame_checksum=Column(String(64),nullable=False)
    seed=Column(Integer,nullable=False)
    frame_size=Column(Integer,nullable=False)
    strata=Column(JSON,nullable=False)
    created_at=Column(String(40),nullable=False)
    created_by=Column(String(120),nullable=False)


class TeardownReceipt(Base):
    __tablename__='teardown_receipts'
    __table_args__=(UniqueConstraint('plan_id','reference','received_at',name='uq_teardown_receipt'),)
    id=Column(Integer,primary_key=True)
    plan_id=Column(Integer,ForeignKey('teardown_plans.id'),nullable=False)
    reference=Column(String(120),nullable=False)
    confirmed_label=Column(String(600),nullable=False)
    received_at=Column(String(40),nullable=False)
    recorded_by=Column(String(120),nullable=False)
    created_at=Column(String(40),nullable=False)


class TeardownEdition(Base):
    __tablename__='teardown_editions'
    __table_args__=(UniqueConstraint('plan_id','version_no',name='uq_teardown_version'),)
    id=Column(Integer,primary_key=True)
    plan_id=Column(Integer,ForeignKey('teardown_plans.id'),nullable=False)
    version_no=Column(Integer,nullable=False)
    receipt_cutoff=Column(String(40),nullable=False)
    state=Column(String(20),default='draft')
    receipt_snapshot=Column(JSON,nullable=False)
    estimates=Column(JSON,nullable=True)
    compute_attempts=Column(Integer,default=0)
    error=Column(Text,default='')
    computed_at=Column(String(40),default='')
    published_at=Column(String(40),default='')
    published_by=Column(String(120),default='')
    created_at=Column(String(40),nullable=False)
    created_by=Column(String(120),nullable=False)


class TeardownStratum(Base):
    __tablename__='teardown_strata'
    __table_args__=(UniqueConstraint('edition_id','stratum_key',name='uq_teardown_stratum'),)
    id=Column(Integer,primary_key=True)
    edition_id=Column(Integer,ForeignKey('teardown_editions.id'),nullable=False)
    stratum_key=Column(String(600),nullable=False)
    population=Column(Integer,nullable=False)
    sample=Column(Integer,nullable=False)
    inclusion_probability=Column(Float,nullable=False)
    references=Column(JSON,nullable=False)
    received=Column(Integer,default=0)
    missing=Column(Integer,default=0)
    missing_references=Column(JSON,default=list)
    misjudged=Column(Integer,default=0)
    weighted_misjudged=Column(Float,default=0)
    status=Column(String(20),default='pending')
    attempts=Column(Integer,default=0)
    computed_at=Column(String(40),default='')
