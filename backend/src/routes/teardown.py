from fastapi import APIRouter,Depends
from ..config.database import db_session
from ..schemas.teardown import TeardownPlanInput,TeardownReceiptBatch,TeardownEditionInput
from ..services.auth import current_user,writer,admin
from ..controllers.teardown import TeardownController as C
router=APIRouter()
@router.get('/teardown/plans')
def plans(db=Depends(db_session),u=Depends(current_user)):return C.list(db)
@router.post('/teardown/plans')
def create_plan(data:TeardownPlanInput,db=Depends(db_session),u=Depends(writer)):return C.create(db,u,data)
@router.get('/teardown/plans/{id}')
def plan_detail(id:int,db=Depends(db_session),u=Depends(current_user)):return C.detail(db,id)
@router.post('/teardown/plans/{id}/receipts')
def add_receipts(id:int,data:TeardownReceiptBatch,db=Depends(db_session),u=Depends(writer)):return C.add_receipts(db,u,id,data)
@router.post('/teardown/plans/{id}/editions')
def create_edition(id:int,data:TeardownEditionInput,db=Depends(db_session),u=Depends(writer)):return C.create_edition(db,u,id,data)
@router.get('/teardown/editions/{id}')
def edition_detail(id:int,db=Depends(db_session),u=Depends(current_user)):return C.edition_detail(db,id)
@router.post('/teardown/editions/{id}/compute')
def compute_edition(id:int,db=Depends(db_session),u=Depends(writer)):return C.compute_edition(db,u,id)
@router.get('/teardown/editions/{id}/recompute')
def recompute_preview(id:int,db=Depends(db_session),u=Depends(current_user)):return C.recompute_preview(db,id)
@router.post('/teardown/editions/{id}/publish')
def publish_edition(id:int,db=Depends(db_session),u=Depends(admin)):return C.publish_edition(db,u,id)
