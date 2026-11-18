"""拆检模块回归：SQLite + TestClient 全流程，并与独立 oracle 对齐。

运行：cd backend && python3 ../test-artifacts/gb-0041/backend/test_teardown_flow.py
依赖：pip install -r requirements.txt httpx
"""
import os
import sys
import tempfile
from decimal import Decimal

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(ROOT, 'backend'))
TMP = tempfile.mkdtemp(prefix='teardown-test-')
os.environ['DATABASE_URL'] = 'sqlite:///' + os.path.join(TMP, 'test.db')
os.environ['ARTIFACT_DIR'] = os.path.join(TMP, 'artifacts')
os.environ['SEED_DEMO'] = 'false'
os.environ['JWT_SECRET'] = 'teardown-test-secret'

from fastapi.testclient import TestClient  # noqa: E402
from src.main import app  # noqa: E402
from src.config.database import Base, engine, Session  # noqa: E402
from src.models.user import User  # noqa: E402
from src.models.source import Source  # noqa: E402
from src.models.run import Run  # noqa: E402
from src.models.teardown import TeardownStratum  # noqa: E402
from src.utils.security import hash_password  # noqa: E402
from src.utils.serialization import now  # noqa: E402
from oracle_teardown import estimate  # noqa: E402

FAILURES = []


def check(name, condition, detail=''):
    print(('PASS ' if condition else 'FAIL ') + name + ((' — ' + str(detail)) if detail and not condition else ''))
    if not condition:
        FAILURES.append(name)


def build_run(rows, labels):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with Session() as db:
        for role in ('admin', 'analyst', 'viewer'):
            db.add(User(email=role + '@t.demo', password_hash=hash_password('Research2026!'), role=role))
        src = Source(key='K-1', name='拆检试验批', filename='a.csv', description='')
        db.add(src)
        db.flush()
        result = {'labels': labels, 'rows': rows, 'checksum': 'oracle-checksum', 'summary': [],
                  'confusion_matrix': [], 'folds': [], 'columns': ['reference'], 'chart': [], 'explanation': '', 'seed': 42}
        db.add(Run(source_id=src.id, name='拆检基线研究', state='completed', parameters={},
                   snapshot={'source': {}, 'records': []}, result=result, created_at=now()))
        db.commit()
    return TestClient(app)


def token(client, role):
    return {'Authorization': 'Bearer ' + client.post('/auth/login', json={
        'email': role + '@t.demo', 'password': 'Research2026!'}).json()['token']}


def truth_map(rows):
    return {r['reference']: r['actual'] for r in rows}


def main():
    rows = ([{'reference': f'N-{i:03}', 'actual': '正常' if i % 2 else '轴承磨损', 'predicted': '正常', 'posterior': .9}
             for i in range(80)] +
            [{'reference': f'E-{i:03}', 'actual': '正常' if i % 2 else '转子偏心', 'predicted': '转子偏心', 'posterior': .8}
             for i in range(10)] +
            [{'reference': f'W-{i:03}', 'actual': '转子偏心' if i == 0 else '轴承磨损', 'predicted': '轴承磨损', 'posterior': .7}
             for i in range(10)])
    client = build_run(rows, ['正常', '转子偏心', '轴承磨损'])
    A, AN, V = token(client, 'admin'), token(client, 'analyst'), token(client, 'viewer')
    truth = truth_map(rows)

    # 权限
    check('viewer 不能建计划', client.post('/teardown/plans', headers=V, json={
        'run_id': 1, 'name': '越权', 'per_stratum_sample': 3}).status_code == 403)

    # 不等比例分层 + 锁定入样概率
    body = {'run_id': 1, 'name': '不等比例拆检', 'seed': 5,
            'sample_sizes': {'正常': 8, '转子偏心': 5, '轴承磨损': 10}}
    plan = client.post('/teardown/plans', headers=AN, json=body).json()['plan']
    pid = plan['id']
    check('计划锁定全批 100 条', plan['frame_size'] == 100, plan['frame_size'])
    pis = {s['key']: s['inclusion_probability'] for s in plan['strata']}
    check('入样概率 8/80、5/10、10/10', abs(pis['正常'] - .1) < 1e-12 and pis['转子偏心'] == .5 and pis['轴承磨损'] == 1.0, pis)
    sampled = [(s['key'], r) for s in plan['strata'] for r in s['references']]
    check('抽取数等于锁定值', len(sampled) == 23, len(sampled))

    # 非法输入
    check('未知计划字段拒绝', client.post('/teardown/plans', headers=AN, json={
        'run_id': 1, 'name': '未知字段', 'per_stratum_sample': 2, 'bogus': 1}).status_code == 422)
    check('全部抽取为 0 拒绝', client.post('/teardown/plans', headers=AN, json={
        'run_id': 1, 'name': '零抽样', 'sample_sizes': {'正常': 0, '转子偏心': 0, '轴承磨损': 0}}).status_code == 422)
    check('层名不在折外判读拒绝', client.post('/teardown/plans', headers=AN, json={
        'run_id': 1, 'name': '错层', 'sample_sizes': {'未知层': 3}}).status_code == 422)
    check('抽取数超过层内全量拒绝', client.post('/teardown/plans', headers=AN, json={
        'run_id': 1, 'name': '超抽', 'sample_sizes': {'正常': 81}}).status_code == 422)
    check('非抽中样本录回件拒绝', client.post(f'/teardown/plans/{pid}/receipts', headers=AN, json={
        'receipts': [{'reference': 'NOPE', 'confirmed_label': '正常'}]}).status_code == 422)
    check('无时区收件时间拒绝', client.post(f'/teardown/plans/{pid}/receipts', headers=AN, json={
        'receipts': [{'reference': sampled[0][1], 'confirmed_label': '正常',
                      'received_at': '2026-11-18T10:00:00'}]}).status_code == 422)

    # 回件幂等
    one = {'receipts': [{'reference': sampled[0][1], 'confirmed_label': truth[sampled[0][1]],
                         'received_at': '2026-11-18T10:00:00+08:00'}]}
    check('首次回件 added=1', client.post(f'/teardown/plans/{pid}/receipts', headers=AN, json=one).json()['added'] == 1)
    check('重复提交 skipped=1', client.post(f'/teardown/plans/{pid}/receipts', headers=AN, json=one).json()['skipped'] == 1)

    # 缺件先不报数
    ed1 = client.post(f'/teardown/plans/{pid}/editions', headers=AN,
                      json={'receipt_cutoff': '2026-11-18T10:15:00+08:00'}).json()
    est1 = client.post(f"/teardown/editions/{ed1['id']}/compute", headers=AN).json()['estimates']
    check('缺回件 reportable=false', est1['reportable'] is False)
    check('缺回件 rate=null', est1['estimated_rate'] is None)
    check('缺件清单逐层列出', any(b.startswith('转子偏心：缺回件') for b in est1['blockers']), est1['blockers'])
    check('缺件版本不可发布', client.post(f"/teardown/editions/{ed1['id']}/publish", headers=A).status_code == 409)

    # 截止顺序
    check('同截止拒绝', client.post(f'/teardown/plans/{pid}/editions', headers=AN,
                                    json={'receipt_cutoff': '2026-11-18T10:15:00+08:00'}).status_code == 409)
    check('早截止拒绝', client.post(f'/teardown/plans/{pid}/editions', headers=AN,
                                    json={'receipt_cutoff': '2026-11-18T09:00:00+08:00'}).status_code == 409)

    # 补齐回件后计权，与 oracle 对齐
    bulk = [{'reference': r, 'confirmed_label': truth[r], 'received_at': '2026-11-18T10:30:00+08:00'} for _, r in sampled]
    client.post(f'/teardown/plans/{pid}/receipts', headers=AN, json={'receipts': bulk})
    ed2 = client.post(f'/teardown/plans/{pid}/editions', headers=AN,
                      json={'receipt_cutoff': '2026-11-18T11:00:00+08:00'}).json()
    est2 = client.post(f"/teardown/editions/{ed2['id']}/compute", headers=AN).json()['estimates']
    oracle = estimate([{'key': s['stratum_key'], 'population': s['population'], 'sample': s['sample'],
                        'inclusion_probability': s['inclusion_probability'], 'references': s['references']}
                       for s in ed2['strata']], ed2['receipt_snapshot'])
    check('计权与 oracle 一致',
          abs(Decimal(str(est2['estimated_misjudged'])) - oracle['estimated_misjudged']) < Decimal('1e-9'),
          (est2['estimated_misjudged'], oracle['estimated_misjudged']))
    manual = sum(Decimal(s['misjudged']) / Decimal(str(s['inclusion_probability'])) for s in est2['strata'])
    check('手工 Σ m/π 复算一致', abs(manual - Decimal(str(est2['estimated_misjudged']))) < Decimal('1e-9'))
    check('计权与朴素样本率不同（确有偏差）', abs(est2['estimated_rate'] - est2['naive_sample_rate']) > 1e-6,
          (est2['estimated_rate'], est2['naive_sample_rate']))

    # 重试不重复加权
    with Session() as db:
        before = {s.stratum_key: s.weighted_misjudged for s in db.query(TeardownStratum).filter_by(edition_id=ed2['id'])}
    again = client.post(f"/teardown/editions/{ed2['id']}/compute", headers=AN).json()['estimates']
    with Session() as db:
        after = {s.stratum_key: s.weighted_misjudged for s in db.query(TeardownStratum).filter_by(edition_id=ed2['id'])}
    check('重试覆盖而非累加', before == after and again['estimated_misjudged'] == est2['estimated_misjudged'])

    # 中断恢复：重置单层后再跑，结果一致
    with Session() as db:
        first = db.query(TeardownStratum).filter_by(edition_id=ed2['id']).first()
        first.status, first.weighted_misjudged = 'pending', None
        db.commit()
    resumed = client.post(f"/teardown/editions/{ed2['id']}/compute", headers=AN).json()['estimates']
    check('分层中断后恢复一致', resumed['estimated_misjudged'] == est2['estimated_misjudged'])

    # 发布与对账
    check('analyst 不可发布', client.post(f"/teardown/editions/{ed2['id']}/publish", headers=AN).status_code == 403)
    published = client.post(f"/teardown/editions/{ed2['id']}/publish", headers=A)
    check('各层齐后可发布', published.status_code == 200 and published.json()['state'] == 'published')
    check('重复发布 409', client.post(f"/teardown/editions/{ed2['id']}/publish", headers=A).status_code == 409)
    check('已发布不可重算', client.post(f"/teardown/editions/{ed2['id']}/compute", headers=AN).status_code == 409)

    # 晚到记录留给下一版；旧版复算不变
    client.post(f'/teardown/plans/{pid}/receipts', headers=AN, json={
        'receipts': [{'reference': sampled[1][1], 'confirmed_label': '正常',
                      'received_at': '2026-11-18T12:00:00+08:00'}]})
    detail = client.get(f"/teardown/editions/{ed2['id']}", headers=V).json()
    check('截止后记录计数', detail['after_cutoff_count'] == 1, detail['after_cutoff_count'])
    preview = client.get(f"/teardown/editions/{ed2['id']}/recompute", headers=V).json()
    check('旧版可按当时结论复算且与已存一致', preview['matches_stored'] is True and preview['mismatches'] == [])

    # 零概率层
    zplan = client.post('/teardown/plans', headers=AN, json={
        'run_id': 1, 'name': '零概率层', 'seed': 2, 'sample_sizes': {'正常': 10, '转子偏心': 0, '轴承磨损': 5}}).json()['plan']
    zrefs = [(s['key'], r) for s in zplan['strata'] for r in s['references']]
    client.post(f"/teardown/plans/{zplan['id']}/receipts", headers=AN, json={
        'receipts': [{'reference': r, 'confirmed_label': truth[r], 'received_at': '2026-11-18T10:00:00+08:00'}
                     for _, r in zrefs]})
    zed = client.post(f"/teardown/plans/{zplan['id']}/editions", headers=AN,
                      json={'receipt_cutoff': '2026-11-18T11:00:00+08:00'}).json()
    zest = client.post(f"/teardown/editions/{zed['id']}/compute", headers=AN).json()['estimates']
    check('零概率层不报数', zest['reportable'] is False and zest['estimated_rate'] is None)
    check('零概率层列入阻断', any('入样概率为 0' in b for b in zest['blockers']), zest['blockers'])
    check('零概率层不可发布', client.post(f"/teardown/editions/{zed['id']}/publish", headers=A).status_code == 409)

    # 审计
    actions = {e['action'] for e in client.get('/audit', headers=A).json()}
    check('计权与发布写入审计', {'teardown.plan.created', 'teardown.receipt.recorded',
                                  'teardown.edition.created', 'teardown.edition.computed',
                                  'teardown.edition.published'} <= actions, actions)

    print()
    if FAILURES:
        print(f'{len(FAILURES)} 项失败: ' + '、'.join(FAILURES))
        return 1
    print('全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
