"""独立口径 oracle：不导入后端服务，仅按文档公式复算拆检误判率。

公式：层内计权误判数 = m_h / pi_h；全批误判数 = Σ_h m_h / pi_h；
全批误判率 = 全批误判数 / N；任一缺回件或 pi_h <= 0 时不可报数。
"""
from decimal import Decimal


def estimate(strata, snapshot):
    """strata: [{key, population, sample, inclusion_probability, references}]
    snapshot: {reference: {'confirmed_label': ...}}
    返回与接口同构的估计字典。"""
    rows = []
    blockers = []
    for s in strata:
        pi = Decimal(str(s['inclusion_probability']))
        missing = [r for r in s['references'] if r not in snapshot]
        received = len(s['references']) - len(missing)
        misjudged = sum(1 for r in s['references']
                        if r in snapshot and str(snapshot[r]['confirmed_label']) != str(s['key']))
        if pi <= 0:
            blockers.append({'key': s['key'], 'kind': 'zero', 'detail': 0})
            weighted = None
        elif len(missing) > 0:
            blockers.append({'key': s['key'], 'kind': 'missing', 'detail': len(missing)})
            weighted = None
        else:
            weighted = Decimal(misjudged) / pi
        rows.append({'key': s['key'], 'population': s['population'], 'sample': s['sample'],
                     'pi': pi, 'received': received, 'missing': len(missing),
                     'misjudged': misjudged, 'weighted': weighted})
    frame = sum(int(s['population']) for s in strata)
    reportable = not blockers
    total = sum(r['weighted'] for r in rows) if reportable else None
    return {'frame_size': frame, 'sampled': sum(int(s['sample']) for s in strata),
            'received': sum(r['received'] for r in rows),
            'misjudged_in_sample': sum(r['misjudged'] for r in rows),
            'reportable': reportable, 'blockers': blockers,
            'estimated_misjudged': total,
            'estimated_rate': (total / Decimal(frame)) if reportable and frame else None,
            'naive_sample_rate': (Decimal(sum(r['misjudged'] for r in rows)) / Decimal(sum(r['received'] for r in rows))
                                  if sum(r['received'] for r in rows) else None)}
