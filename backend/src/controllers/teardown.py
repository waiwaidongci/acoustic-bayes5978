from ..services import teardown


class TeardownController:
    list=staticmethod(teardown.list_plans)
    create=staticmethod(teardown.create_plan)
    detail=staticmethod(teardown.plan_detail)
    add_receipts=staticmethod(teardown.add_receipts)
    create_edition=staticmethod(teardown.create_edition)
    edition_detail=staticmethod(teardown.edition_detail)
    compute_edition=staticmethod(teardown.compute_edition)
    recompute_preview=staticmethod(teardown.recompute_preview)
    publish_edition=staticmethod(teardown.publish_edition)
