import warnings
from decimal import Decimal

import agate

warnings.simplefilter('always')  # 让 agate 的 null 警告显示出来

number = agate.Number()
text = agate.Text()

# 小表：A 组含 0 和缺失值；B 组正常；C 组整组 value 全空
rows = [
    ('A', Decimal('0')),    # 零值
    ('A', Decimal('10')),
    ('A', None),            # 缺失值
    ('B', Decimal('20')),
    ('B', Decimal('30')),
    ('C', None),            # 全空分组
    ('C', None),
]
table = agate.Table(rows, ['grp', 'value'], [text, number])
print('=== 原始表 ===')
table.print_table()

print('=== 按组聚合：Mean / Count() / Count(value) ===')
grouped = table.group_by('grp').aggregate([
    ('mean', agate.Mean('value')),
    ('count_rows', agate.Count()),            # 行数
    ('count_nonnull', agate.Count('value')),  # 非空个数
    ('sum', agate.Sum('value')),
])
grouped.print_table()

print('=== 在聚合结果上 compute Percent（分母=该表 count_rows 列的 Sum）===')
with_pct = grouped.compute([
    ('pct_of_rows', agate.Percent('count_rows')),
])
with_pct.print_table()

print('=== 顺序对比：先 where 再聚合 vs 先聚合再筛选 ===')
# 路径 1：先 where 再聚合
path1 = table.where(lambda r: r['grp'] != 'A').group_by('grp').aggregate([
    ('mean', agate.Mean('value')),
    ('count_rows', agate.Count()),
])
print('--- 先 where(去掉A) 再聚合 ---')
path1.print_table()
print('--- 再对 path1 算百分比（分母只剩 B、C 的行数和）---')
path1.compute([('pct', agate.Percent('count_rows'))]).print_table()

# 路径 2：先聚合再筛选
path2 = table.group_by('grp').aggregate([
    ('mean', agate.Mean('value')),
    ('count_rows', agate.Count()),
]).where(lambda r: r['grp'] != 'A')
print('--- 先聚合再 where(去掉A) ---')
path2.print_table()
print('--- 若百分比在全表上先算好再筛选（分母含 A）---')
path2b = table.group_by('grp').aggregate([
    ('count_rows', agate.Count()),
]).compute([('pct', agate.Percent('count_rows'))]).where(lambda r: r['grp'] != 'A')
path2b.print_table()
