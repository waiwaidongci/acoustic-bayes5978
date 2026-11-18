import random
from datetime import datetime, timezone
from fastapi import HTTPException
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from ..models.teardown import TeardownPlan, TeardownReceipt, TeardownEdition, TeardownStratum
from ..utils.serialization import row, now
from .audit import log
from .runs import get_run


def _ts(value=None):
    if value is None or value == '':
        moment = datetime.now(timezone.utc)
    else:
        if not isinstance(value, str):
            raise HTTPException(422, '时间必须为 ISO8601 字符串')
        text = value.strip()
        if text.endswith('Z'):
            text = text[:-1] + '+00:00'
        try:
            moment = datetime.fromisoformat(text)
        except ValueError:
            raise HTTPException(422, '时间格式需为 ISO8601，例如 2026-11-18T10:00:00+08:00')
        if moment.tzinfo is None:
            raise HTTPException(422, '时间必须带时区，避免封账口径漂移')
        moment = moment.astimezone(timezone.utc)
    return moment.isoformat(timespec='microseconds')


def _frame(run):
    if run.state != 'completed' or not run.result:
        raise HTTPException(409, '仅可按已完成研究冻结的折外判读抽样')
    rows = run.result.get('rows') or []
    if not rows:
        raise HTTPException(422, '研究结果没有折外判读行')
    labels = [str(x) for x in (run.result.get('labels') or [])]
    groups = {}
    seen = set()
    for item in rows:
        reference = str(item.get('reference') or '').strip()
        if not reference:
            raise HTTPException(422, '折外判读缺少样本编号')
        if reference in seen:
            raise HTTPException(422, '折外判读存在重复样本编号：' + reference)
        seen.add(reference)
        groups.setdefault(str(item.get('predicted')), []).append(reference)
    if not groups:
        raise HTTPException(422, '折外判读没有可抽样行')
    order = [x for x in labels if x in groups] + sorted(x for x in groups if x not in labels)
    return rows, order, {key: sorted(refs) for key, refs in groups.items()}, run.result.get('checksum') or ''


def get_plan(db, id):
    plan = db.get(TeardownPlan, id)
    if not plan:
        raise HTTPException(404, '未找到拆检计划')
    return plan


def _sampled_map(plan):
    sampled = {}
    for stratum in plan.strata:
        for reference in stratum['references']:
            sampled[reference] = stratum['key']
    return sampled


def _edition_summary(edition):
    estimates = edition.estimates or {}
    return {'id': edition.id, 'version_no': edition.version_no, 'receipt_cutoff': edition.receipt_cutoff,
            'state': edition.state, 'reportable': estimates.get('reportable'), 'estimated_rate': estimates.get('estimated_rate'),
            'created_at': edition.created_at, 'published_at': edition.published_at}


def plan_detail(db, id):
    plan = get_plan(db, id)
    receipts = db.scalars(select(TeardownReceipt).where(TeardownReceipt.plan_id == plan.id)
                          .order_by(TeardownReceipt.received_at.desc(), TeardownReceipt.id.desc())).all()
    editions = db.scalars(select(TeardownEdition).where(TeardownEdition.plan_id == plan.id)
                          .order_by(TeardownEdition.version_no.desc())).all()
    return {'plan': row(plan), 'sampled': sum(int(x['sample']) for x in plan.strata),
            'receipt_count': len(receipts), 'receipts': [row(x) for x in receipts],
            'editions': [_edition_summary(x) for x in editions]}


def list_plans(db):
    plans = db.scalars(select(TeardownPlan).order_by(TeardownPlan.id.desc())).all()
    rows = []
    for plan in plans:
        receipt_count = db.scalar(select(func.count()).select_from(TeardownReceipt).where(TeardownReceipt.plan_id == plan.id))
        editions = db.scalars(select(TeardownEdition).where(TeardownEdition.plan_id == plan.id)
                              .order_by(TeardownEdition.version_no.desc())).all()
        rows.append({'id': plan.id, 'name': plan.name, 'run_id': plan.run_id, 'run_name': plan.run_name,
                     'frame_size': plan.frame_size, 'seed': plan.seed, 'sampled': sum(int(x['sample']) for x in plan.strata),
                     'stratum_count': len(plan.strata), 'receipt_count': receipt_count or 0,
                     'created_at': plan.created_at, 'created_by': plan.created_by,
                     'editions': [_edition_summary(x) for x in editions]})
    return rows


def create_plan(db, user, data):
    run = get_run(db, data.run_id)
    rows, order, groups, checksum = _frame(run)
    requested = data.sample_sizes or {}
    unknown = [key for key in requested if key not in groups]
    if unknown:
        raise HTTPException(422, '抽样层不在折外判读中：' + '、'.join(str(x) for x in unknown))
    sizes = {}
    for label in order:
        size = requested[label] if label in requested else data.per_stratum_sample
        population = len(groups[label])
        if isinstance(size, bool) or not isinstance(size, int):
            raise HTTPException(422, '各层抽取数必须为整数')
        if size < 0 or size > population:
            raise HTTPException(422, f'{label}层抽取数需在0至{population}之间')
        sizes[label] = size
    if sum(sizes.values()) == 0:
        raise HTTPException(422, '至少需要抽取一条样本，否则全批误判率不可估计')
    seed = int(data.seed)
    strata = []
    for label in order:
        population = len(groups[label])
        size = sizes[label]
        picked = random.Random(f'{seed}|{label}').sample(groups[label], size)
        strata.append({'key': label, 'population': population, 'sample': size,
                       'inclusion_probability': (size / population if population else 0.0),
                       'references': sorted(picked), 'sampling': '按折外预测判读分层，层内无放回随机抽样'})
    plan = TeardownPlan(run_id=run.id, name=data.name, run_name=run.name, frame_checksum=checksum, seed=seed,
                        frame_size=len(rows), strata=strata, created_at=now(), created_by=user.email)
    db.add(plan)
    db.flush()
    log(db, user, 'teardown.plan.created', plan.id, data.name)
    db.commit()
    return plan_detail(db, plan.id)


def add_receipts(db, user, plan_id, data):
    plan = get_plan(db, plan_id)
    sampled = _sampled_map(plan)
    allowed = {stratum['key'] for stratum in plan.strata}
    added, skipped = [], []
    for item in data.receipts:
        reference = item.reference.strip()
        if reference not in sampled:
            raise HTTPException(422, '样本不在该计划的抽中名单中：' + reference)
        label = item.confirmed_label.strip()
        if label not in allowed:
            raise HTTPException(422, '拆检结论需为该研究的判读类型：' + label)
        received_at = _ts(item.received_at)
        existing = db.scalar(select(TeardownReceipt).where(TeardownReceipt.plan_id == plan.id,
                                                          TeardownReceipt.reference == reference,
                                                          TeardownReceipt.received_at == received_at))
        if existing and existing.confirmed_label == label:
            skipped.append(reference)
            continue
        if existing:
            raise HTTPException(409, '同一收件时刻已有不同拆检结论，请用新的收到时间记录更正：' + reference)
        db.add(TeardownReceipt(plan_id=plan.id, reference=reference, confirmed_label=label,
                               received_at=received_at, recorded_by=user.email, created_at=now()))
        added.append(reference)
    if added:
        log(db, user, 'teardown.receipt.recorded', plan.id, f'{len(added)} 件')
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, '回件并发写入冲突，请重新提交')
    return {'added': len(added), 'skipped': len(skipped), 'references': added}


def create_edition(db, user, plan_id, data):
    plan = get_plan(db, plan_id)
    db.execute(select(TeardownPlan.id).where(TeardownPlan.id == plan.id).with_for_update())
    cutoff = _ts(data.receipt_cutoff)
    previous = db.scalar(select(func.max(TeardownEdition.receipt_cutoff)).where(TeardownEdition.plan_id == plan.id))
    if previous and cutoff <= previous:
        raise HTTPException(409, '收件截止需晚于上一版，晚到记录留给下一版')
    snapshot = _snapshot_at(db, plan.id, cutoff)
    last = db.scalar(select(func.max(TeardownEdition.version_no)).where(TeardownEdition.plan_id == plan.id)) or 0
    edition = TeardownEdition(plan_id=plan.id, version_no=last + 1, receipt_cutoff=cutoff, state='draft',
                              receipt_snapshot=snapshot, created_at=now(), created_by=user.email)
    db.add(edition)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, '版本号并发冲突，请重试封账')
    for stratum in plan.strata:
        db.add(TeardownStratum(edition_id=edition.id, stratum_key=stratum['key'], population=stratum['population'],
                               sample=stratum['sample'], inclusion_probability=stratum['inclusion_probability'],
                               references=stratum['references'], status='pending'))
    log(db, user, 'teardown.edition.created', edition.id, f'第 {edition.version_no} 版 · 截止 {cutoff}')
    db.commit()
    return edition_detail(db, edition.id)


def get_edition(db, id):
    edition = db.get(TeardownEdition, id)
    if not edition:
        raise HTTPException(404, '未找到拆检版本')
    return edition


def edition_detail(db, id):
    edition = get_edition(db, id)
    plan = get_plan(db, edition.plan_id)
    strata = db.scalars(select(TeardownStratum).where(TeardownStratum.edition_id == edition.id)
                        .order_by(TeardownStratum.id)).all()
    snapshot = edition.receipt_snapshot or {}
    late = db.scalars(select(TeardownReceipt).where(TeardownReceipt.plan_id == plan.id,
                                                    TeardownReceipt.received_at <= edition.receipt_cutoff)
                      .order_by(TeardownReceipt.received_at, TeardownReceipt.id)).all()
    latest = {}
    for receipt in late:
        latest[receipt.reference] = receipt
    pending_late = sorted(reference for reference, receipt in latest.items()
                          if (snapshot.get(reference) or {}).get('receipt_id') != receipt.id)
    after_cutoff = db.scalar(select(func.count()).select_from(TeardownReceipt)
                             .where(TeardownReceipt.plan_id == plan.id,
                                    TeardownReceipt.received_at > edition.receipt_cutoff)) or 0
    data = row(edition)
    data['plan'] = {'id': plan.id, 'name': plan.name, 'run_id': plan.run_id, 'run_name': plan.run_name,
                    'frame_size': plan.frame_size, 'seed': plan.seed, 'frame_checksum': plan.frame_checksum}
    data['strata'] = [row(x) for x in strata]
    data['pending_late'] = pending_late
    data['after_cutoff_count'] = after_cutoff
    return data


def _estimate(spec, snapshot):
    key = str(spec['key'])
    pi = float(spec['inclusion_probability'])
    references = list(spec['references'])
    received = 0
    misjudged = 0
    missing = []
    for reference in references:
        conclusion = snapshot.get(str(reference))
        if not conclusion:
            missing.append(reference)
            continue
        received += 1
        if str(conclusion.get('confirmed_label')) != key:
            misjudged += 1
    if pi <= 0:
        status, reason, weighted = 'blocked', '零入样概率层', None
    elif missing:
        status, reason, weighted = 'blocked', f'缺回件 {len(missing)} 件', None
    else:
        status, reason, weighted = 'computed', '', misjudged / pi
    return {'key': key, 'population': int(spec['population']), 'sample': int(spec['sample']),
            'inclusion_probability': pi, 'received': received, 'missing': len(missing),
            'missing_references': missing, 'misjudged': misjudged, 'weighted': weighted,
            'status': status, 'reason': reason}


def _totals(results):
    frame_size = sum(int(x['population']) for x in results)
    blockers = []
    for item in results:
        if float(item['inclusion_probability']) <= 0:
            blockers.append(f"{item['key']}：入样概率为 0，无法代表全层")
        elif item['missing'] > 0:
            blockers.append(f"{item['key']}：缺回件 {item['missing']} 件")
        elif item['status'] != 'computed':
            blockers.append(f"{item['key']}：未完成分层计权")
    reportable = not blockers
    estimated_total = sum(float(x['weighted']) for x in results) if reportable else None
    received = sum(int(x['received']) for x in results)
    misjudged = sum(int(x['misjudged']) for x in results)
    return {'frame_size': frame_size, 'sampled': sum(int(x['sample']) for x in results),
            'received': received, 'misjudged_in_sample': misjudged, 'reportable': reportable,
            'blockers': blockers, 'estimated_misjudged': estimated_total,
            'estimated_rate': (estimated_total / frame_size if reportable and frame_size else None),
            'naive_sample_rate': (misjudged / received if reportable and received else None),
            'strata': results}


def _stored_result(stratum):
    return {'key': stratum.stratum_key, 'population': stratum.population, 'sample': stratum.sample,
            'inclusion_probability': float(stratum.inclusion_probability), 'received': stratum.received or 0,
            'missing': stratum.missing or 0, 'missing_references': stratum.missing_references or [],
            'misjudged': stratum.misjudged or 0, 'weighted': stratum.weighted_misjudged, 'status': stratum.status}


def _snapshot_at(db, plan_id, cutoff):
    receipts = db.scalars(select(TeardownReceipt)
                          .where(TeardownReceipt.plan_id == plan_id, TeardownReceipt.received_at <= cutoff)
                          .order_by(TeardownReceipt.received_at, TeardownReceipt.id)).all()
    snapshot = {}
    for receipt in receipts:
        snapshot[receipt.reference] = {'confirmed_label': receipt.confirmed_label,
                                       'received_at': receipt.received_at, 'receipt_id': receipt.id}
    return snapshot


def compute_edition(db, user, id):
    edition = get_edition(db, id)
    if edition.state != 'draft':
        raise HTTPException(409, '已发布版本不可重算；如需更正请创建下一版')
    # 封账与更正并发时，按收件截止时刻的版本取值；晚于截止的记录不进入本版。
    snapshot = _snapshot_at(db, edition.plan_id, edition.receipt_cutoff)
    edition.receipt_snapshot = snapshot
    strata = db.scalars(select(TeardownStratum).where(TeardownStratum.edition_id == edition.id)
                        .order_by(TeardownStratum.id)).all()
    edition.compute_attempts = (edition.compute_attempts or 0) + 1
    processed = 0
    for stratum in strata:
        spec = {'key': stratum.stratum_key, 'population': stratum.population, 'sample': stratum.sample,
                'inclusion_probability': float(stratum.inclusion_probability), 'references': stratum.references}
        # 每层计权是覆盖赋值而非累加，恢复重试不会重复加权。
        result = _estimate(spec, snapshot)
        stratum.received = result['received']
        stratum.missing = result['missing']
        stratum.missing_references = result['missing_references']
        stratum.misjudged = result['misjudged']
        stratum.weighted_misjudged = result['weighted']
        stratum.status = result['status']
        stratum.attempts = (stratum.attempts or 0) + 1
        stratum.computed_at = now()
        db.commit()
        processed += 1
    totals = _totals([_stored_result(x) for x in strata])
    edition.estimates = totals
    edition.error = '' if totals['reportable'] else '；'.join(totals['blockers'])
    edition.computed_at = now()
    log(db, user, 'teardown.edition.computed', edition.id, f'本次计权 {processed} 层')
    db.commit()
    return edition_detail(db, edition.id)


def _close(left, right):
    if left is None or right is None:
        return left is None and right is None
    return abs(float(left) - float(right)) <= 1e-12 * max(1.0, abs(float(right)))


def _edition_snapshot(db, edition):
    # 草稿按当前收件截止时刻取值，可采纳截止前的更正；已发布版本固定为发布时的冻结快照，
    # 之后再补录的倒签记录也不会改写旧版。
    if edition.state == 'published':
        return edition.receipt_snapshot or {}
    return _snapshot_at(db, edition.plan_id, edition.receipt_cutoff)


def _reconcile(db, edition, snapshot):
    plan = get_plan(db, edition.plan_id)
    strata = db.scalars(select(TeardownStratum).where(TeardownStratum.edition_id == edition.id)
                        .order_by(TeardownStratum.id)).all()
    if len(strata) != len(plan.strata):
        raise HTTPException(409, '分层数与计划锁定值不一致，请重建版本')
    by_key = {stratum.stratum_key: stratum for stratum in strata}
    if set(by_key) != {stratum['key'] for stratum in plan.strata}:
        raise HTTPException(409, '分层与计划锁定值不一致，请重建版本')
    mismatches = []
    recomputed = []
    for spec in plan.strata:
        stored = by_key[spec['key']]
        result = _estimate(spec, snapshot)
        recomputed.append(result)
        aligned = (stored.population == result['population'] and stored.sample == result['sample']
                   and _close(stored.inclusion_probability, result['inclusion_probability'])
                   and stored.status == result['status'] and (stored.received or 0) == result['received']
                   and (stored.missing or 0) == result['missing'] and (stored.misjudged or 0) == result['misjudged']
                   and _close(stored.weighted_misjudged, result['weighted']))
        if not aligned:
            mismatches.append(spec['key'])
    return recomputed, mismatches


def recompute_preview(db, id):
    edition = get_edition(db, id)
    recomputed, mismatches = _reconcile(db, edition, _edition_snapshot(db, edition))
    totals = _totals(recomputed)
    stored = edition.estimates or {}
    return {'edition_id': edition.id, 'version_no': edition.version_no, 'receipt_cutoff': edition.receipt_cutoff,
            'state': edition.state, 'source': '按本版冻结的收件截止快照复算，不改写任何已存计权',
            'estimate': totals, 'mismatches': mismatches,
            'matches_stored': not mismatches and (not stored or
                                                  (_close(stored.get('estimated_rate'), totals['estimated_rate'])
                                                   and bool(stored.get('reportable')) == bool(totals['reportable'])))}


def publish_edition(db, user, id):
    edition = get_edition(db, id)
    if edition.state == 'published':
        raise HTTPException(409, '该版本已经发布')
    recomputed, mismatches = _reconcile(db, edition, _edition_snapshot(db, edition))
    if mismatches:
        raise HTTPException(409, '各层计权与冻结快照未对齐，请重新执行分层计权：' + '、'.join(mismatches))
    totals = _totals(recomputed)
    if not totals['reportable']:
        raise HTTPException(409, '各层尚未对账齐，暂不发布：' + '；'.join(totals['blockers']))
    edition.estimates = totals
    edition.state = 'published'
    edition.published_at = now()
    edition.published_by = user.email
    edition.error = ''
    log(db, user, 'teardown.edition.published', edition.id, f'第 {edition.version_no} 版')
    db.commit()
    return edition_detail(db, edition.id)
