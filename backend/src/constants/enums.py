from enum import Enum
class Role(str,Enum):
    admin='admin'; analyst='analyst'; viewer='viewer'
class RunState(str,Enum):
    queued='queued'; running='running'; completed='completed'; failed='failed'
class ReviewState(str,Enum):
    draft='draft'; approved='approved'
class TeardownEditionState(str,Enum):
    draft='draft'; published='published'
class TeardownStratumState(str,Enum):
    pending='pending'; computed='computed'; blocked='blocked'
