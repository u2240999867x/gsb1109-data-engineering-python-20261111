"""ETL 输入分块并行: 四类迭代操作的切块边界对照样例.

用同一份输入 ``DATA``, 在每一个短切点 ``k = 0..len(DATA)`` 上切成
head/tail 两块, 比较三种结果:

1. 不切时的整体结果 (基准);
2. 两块各自独立处理后直接拼接 (朴素并行, 不携带任何历史);
3. 只携带"最小必要历史"的分块结果 (有状态并行).

有状态方案只传递有界或最小的摘要 (末组信息、末 n-1 个元素、已见
key 集合), 函数签名里只有 head/tail, 绝不把整份原输入当作块上下文.

直接运行可打印逐切点对照, ``main()`` 中的断言让结论可执行复现.
"""

from itertools import groupby

from more_itertools import unique_everseen, windowed

DATA = [1, 1, 2, 2, 1, 3]
WINDOW_SIZE = 3
_PAD = object()


def split_at(seq, index):
    """在 index 处切成 (前 index 个元素, 其余元素) 两块."""
    return seq[:index], seq[index:]


def cut_points(seq):
    """全部短切点 0..len(seq), 含 head 或 tail 为空的退化切点."""
    return range(len(seq) + 1)


# --- 1. 普通映射: 无状态, 元素一一对应, 不依赖任何邻居 ---------------

def double(value):
    return value * 2


def whole_map(seq):
    return list(map(double, seq))


def map_naive_concat(head, tail):
    return list(map(double, head)) + list(map(double, tail))


# --- 2. 相邻分组 (itertools.groupby): 依赖"上一个元素" ---------------

def whole_adjacent_groups(seq):
    return [list(group) for _, group in groupby(seq)]


def adjacent_naive_concat(head, tail):
    # 两块各自分组后直接拼: 被切点劈开的同值相邻组会变成两组.
    return whole_adjacent_groups(head) + whole_adjacent_groups(tail)


def adjacent_stateful_concat(head, tail):
    # 最小历史: 末组的 (key, 元素计数), 有界 O(1).
    # 若下游只需要组键 (如 unique_justseen), 状态可缩成"末组 key".
    head_groups = [(key, len(list(group))) for key, group in groupby(head)]
    tail_groups = [(key, len(list(group))) for key, group in groupby(tail)]
    if head_groups and tail_groups and head_groups[-1][0] == tail_groups[0][0]:
        merged_key = head_groups[-1][0]
        merged_count = head_groups[-1][1] + tail_groups[0][1]
        groups = head_groups[:-1] + [(merged_key, merged_count)]
        groups += tail_groups[1:]
    else:
        groups = head_groups + tail_groups
    return [[key] * count for key, count in groups]


# --- 3. 滑动窗口 (more_itertools.windowed, step=1) -------------------

def whole_windows(seq):
    return list(windowed(seq, WINDOW_SIZE))


def windows_naive_concat(head, tail):
    # 直接拼会同时犯两个错: 块尾被 fillvalue 填充出假窗口,
    # 跨越切点的真实窗口整体丢失.
    return whole_windows(head) + whole_windows(tail)


def _full_windows(seq):
    # 非末尾块只保留完全由真实元素构成的窗口, 丢弃块尾填充窗口.
    return [
        window
        for window in windowed(seq, WINDOW_SIZE, fillvalue=_PAD)
        if _PAD not in window
    ]


def windows_stateful_concat(head, tail):
    # 最小历史: head 末尾 WINDOW_SIZE-1 个原始元素, 有界 O(n).
    # 更早的历史对任何窗口都不可见, 无需携带.
    head = list(head)
    tail = list(tail)
    carry = head[-(WINDOW_SIZE - 1):]
    result = _full_windows(head)
    if tail:
        # tail 是最后一块, 保留它在序列末端的填充窗口, 与整体语义一致.
        result += list(windowed(carry + tail, WINDOW_SIZE))
    return result


# --- 4. 去重 (unique_everseen): 依赖"此前出现过的全部值" -------------

def whole_dedup(seq):
    return list(unique_everseen(seq))


def dedup_naive_concat(head, tail):
    # tail 里重现 head 已出现过的值时, 会被再次输出.
    return whole_dedup(head) + whole_dedup(tail)


def dedup_stateful_concat(head, tail):
    # 最小历史: head 中已见 key 的集合 (无 key 函数时就是元素本身;
    # 有 key 函数时存 key(element)). 该集合随去重后基数增长, 无有界
    # 上界, 这是去重无法像 map 一样廉价并行的根本原因.
    head_result = list(unique_everseen(head))
    seen = set(head)
    tail_result = []
    for element in tail:
        if element not in seen:
            seen.add(element)
            tail_result.append(element)
    return head_result + tail_result


# --- 逐切点对照与输出 -------------------------------------------------

def _evaluate(naive_fn, stateful_fn, whole_result):
    rows = []
    for index in cut_points(DATA):
        head, tail = split_at(DATA, index)
        naive = naive_fn(head, tail)
        stateful = (
            stateful_fn(head, tail) if stateful_fn is not None else None
        )
        rows.append(
            (
                index,
                head,
                tail,
                naive == whole_result,
                None if stateful is None else stateful == whole_result,
                naive,
                stateful,
            )
        )
    return rows


def _print_section(title, whole_result, rows, has_state):
    print(f"=== {title} ===")
    print(f"整体结果: {whole_result}")
    if has_state:
        print("切点 | head | tail | 朴素拼接等价 | 携带最小历史等价")
    else:
        print("切点 | head | tail | 朴素拼接等价")
    for index, head, tail, naive_ok, stateful_ok, naive, _ in rows:
        stateful_text = ""
        if has_state:
            stateful_text = f" | {str(stateful_ok):>5}"
        print(
            f"  {index}  | {head} | {tail} | "
            f"{str(naive_ok):>5}{stateful_text}"
        )
        if not naive_ok:
            print(f"        反例: 朴素拼接得到 {naive}")
    print()


def main():
    whole_map_result = whole_map(DATA)
    whole_group_result = whole_adjacent_groups(DATA)
    whole_window_result = whole_windows(DATA)
    whole_dedup_result = whole_dedup(DATA)

    _print_section(
        "1. 普通映射 map(double): 无状态, 可直接并行",
        whole_map_result,
        _evaluate(map_naive_concat, None, whole_map_result),
        has_state=False,
    )
    _print_section(
        "2. 相邻分组 groupby: 携带末组 (key, 计数) 后等价",
        whole_group_result,
        _evaluate(
            adjacent_naive_concat,
            adjacent_stateful_concat,
            whole_group_result,
        ),
        has_state=True,
    )
    _print_section(
        "3. 滑动窗口 windowed(n=3): 携带末 n-1 个元素后等价",
        whole_window_result,
        _evaluate(
            windows_naive_concat,
            windows_stateful_concat,
            whole_window_result,
        ),
        has_state=True,
    )
    _print_section(
        "4. 去重 unique_everseen: 携带已见 key 集合后等价",
        whole_dedup_result,
        _evaluate(
            dedup_naive_concat,
            dedup_stateful_concat,
            whole_dedup_result,
        ),
        has_state=True,
    )

    # 硬断言: 携带最小历史的方案在每一个短切点都与不切时等价.
    for index in cut_points(DATA):
        head, tail = split_at(DATA, index)
        assert map_naive_concat(head, tail) == whole_map_result
        assert adjacent_stateful_concat(head, tail) == whole_group_result
        assert windows_stateful_concat(head, tail) == whole_window_result
        assert dedup_stateful_concat(head, tail) == whole_dedup_result

    # 反例断言: 朴素直接拼接在这些切点必然不等价.
    assert adjacent_naive_concat([1], [1, 2, 2, 1, 3]) != whole_group_result
    assert windows_naive_concat([1, 1], [2, 2, 1, 3]) != whole_window_result
    assert dedup_naive_concat([1], [1, 2, 2, 1, 3]) != whole_dedup_result

    print("=== 边界总表 ===")
    for row in BOUNDARY_TABLE:
        print(" | ".join(row))
    print()
    print("全部断言通过: 有状态方案在 7 个短切点上均与整体结果等价。")


BOUNDARY_TABLE = [
    ("操作", "朴素切块拼接", "最小跨块历史", "历史规模"),
    (
        "map 普通映射",
        "恒等价",
        "无需任何状态",
        "—",
    ),
    (
        "groupby 相邻分组",
        "仅切点落在组界时等价",
        "末组 (key, 计数); 只取组键时仅末组 key",
        "有界 O(1)",
    ),
    (
        "windowed 滑动窗口",
        "仅空块退化切点等价",
        "前块末尾 n-1 个原始元素 (step=1)",
        "有界 O(n)",
    ),
    (
        "unique_everseen 去重",
        "一般不等价, 尾部重现旧值即重复",
        "已见 key 集合 (有 key 函数时存 key)",
        "无界, 随去重后基数增长",
    ),
]


if __name__ == "__main__":
    main()
