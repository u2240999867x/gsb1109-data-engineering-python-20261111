"""分块并行边界对照的可执行回归测试.

对同一份输入穷举全部短切点, 固定四类操作的等价边界:

* map: 朴素拼接恒等价;
* groupby: 朴素拼接仅在组界切点等价, 携带末组信息后恒等价;
* windowed: 朴素拼接在非退化切点恒不等价, 携带末 n-1 个元素后恒等价;
* unique_everseen: 朴素拼接在 tail 重现 head 已见值时失效,
  携带已见 key 集合后恒等价.
"""

from itertools import groupby
from unittest import TestCase

from more_itertools import unique_everseen, windowed

from etl_chunking_boundary_demo import (
    DATA,
    WINDOW_SIZE,
    adjacent_naive_concat,
    adjacent_stateful_concat,
    cut_points,
    dedup_naive_concat,
    dedup_stateful_concat,
    map_naive_concat,
    whole_adjacent_groups,
    whole_dedup,
    whole_map,
    whole_windows,
    windows_naive_concat,
    windows_stateful_concat,
)


class MapChunkingTests(TestCase):
    def test_naive_concat_matches_at_every_cut_point(self):
        expected = whole_map(DATA)
        for index in cut_points(DATA):
            head, tail = DATA[:index], DATA[index:]
            with self.subTest(cut=index):
                self.assertEqual(map_naive_concat(head, tail), expected)


class AdjacentGroupingChunkingTests(TestCase):
    def test_naive_concat_only_equivalent_at_group_boundaries(self):
        expected = whole_adjacent_groups(DATA)
        equivalent_cuts = set()
        for index in cut_points(DATA):
            head, tail = DATA[:index], DATA[index:]
            if adjacent_naive_concat(head, tail) == expected:
                equivalent_cuts.add(index)
        # DATA 的组界: 切点 0, 2, 4, 5, 6; 切点 1 和 3 劈开同值相邻组.
        self.assertEqual(equivalent_cuts, {0, 2, 4, 5, 6})

    def test_naive_concat_splits_one_group_into_two(self):
        expected = whole_adjacent_groups(DATA)
        head, tail = DATA[:1], DATA[1:]
        self.assertNotEqual(adjacent_naive_concat(head, tail), expected)
        self.assertEqual(
            adjacent_naive_concat(head, tail),
            [[1], [1], [2, 2], [1], [3]],
        )

    def test_stateful_concat_matches_at_every_cut_point(self):
        expected = whole_adjacent_groups(DATA)
        for index in cut_points(DATA):
            head, tail = DATA[:index], DATA[index:]
            with self.subTest(cut=index):
                self.assertEqual(
                    adjacent_stateful_concat(head, tail), expected
                )

    def test_required_history_is_only_the_last_group(self):
        head, _ = DATA[:3], DATA[3:]
        head_groups = [
            (key, len(list(group))) for key, group in groupby(head)
        ]
        # 跨块只需要末组, 而不是 head 的完整分组序列.
        self.assertEqual(head_groups[-1], (2, 1))
        self.assertLess(len(head_groups), len(head))


class SlidingWindowChunkingTests(TestCase):
    def test_naive_concat_never_equivalent_on_nontrivial_cuts(self):
        expected = whole_windows(DATA)
        equivalent_cuts = set()
        for index in cut_points(DATA):
            head, tail = DATA[:index], DATA[index:]
            if windows_naive_concat(head, tail) == expected:
                equivalent_cuts.add(index)
        # 只有 head 或 tail 为空的退化切点幸免.
        self.assertEqual(equivalent_cuts, {0, len(DATA)})

    def test_naive_concat_invents_filled_windows_and_drops_crossing_ones(self):
        expected = whole_windows(DATA)
        head, tail = DATA[:2], DATA[2:]
        naive = windows_naive_concat(head, tail)
        self.assertNotEqual(naive, expected)
        # 块尾填充窗口是整体结果中不存在的假窗口.
        self.assertIn((1, 1, None), naive)
        # 跨越切点的 (1, 2, 2) 整体丢失.
        self.assertNotIn((1, 2, 2), naive)

    def test_stateful_concat_matches_at_every_cut_point(self):
        expected = whole_windows(DATA)
        for index in cut_points(DATA):
            head, tail = DATA[:index], DATA[index:]
            with self.subTest(cut=index):
                self.assertEqual(
                    windows_stateful_concat(head, tail), expected
                )

    def test_required_history_bounded_to_n_minus_one_elements(self):
        long_head = list(range(1000))
        carry = long_head[-(WINDOW_SIZE - 1):]
        # 无论 head 多长, 跨块历史恒为末尾 n-1 个元素, 不携带整块输入.
        self.assertEqual(len(carry), WINDOW_SIZE - 1)
        self.assertEqual(carry, [998, 999])

    def test_windows_ending_at_true_end_keep_fillvalue_semantics(self):
        # tail 为最后一块时, 序列末端的填充窗口必须保留.
        head, tail = DATA[:5], DATA[5:]
        result = windows_stateful_concat(head, tail)
        self.assertEqual(result, whole_windows(DATA))
        self.assertEqual(result[-1], (2, 1, 3))


class DedupChunkingTests(TestCase):
    def test_naive_concat_fails_when_tail_repeats_seen_values(self):
        expected = whole_dedup(DATA)
        equivalent_cuts = set()
        for index in cut_points(DATA):
            head, tail = DATA[:index], DATA[index:]
            if dedup_naive_concat(head, tail) == expected:
                equivalent_cuts.add(index)
        # 切点 1..4 的 tail 都重现了 head 已见值; 0/5/6 侥幸等价.
        self.assertEqual(equivalent_cuts, {0, 5, 6})

    def test_naive_concat_re_emits_head_value(self):
        expected = whole_dedup(DATA)
        head, tail = DATA[:1], DATA[1:]
        self.assertNotEqual(dedup_naive_concat(head, tail), expected)
        self.assertEqual(dedup_naive_concat(head, tail), [1, 1, 2, 3])

    def test_stateful_concat_matches_at_every_cut_point(self):
        expected = whole_dedup(DATA)
        for index in cut_points(DATA):
            head, tail = DATA[:index], DATA[index:]
            with self.subTest(cut=index):
                self.assertEqual(dedup_stateful_concat(head, tail), expected)

    def test_required_history_is_seen_key_set_not_whole_block(self):
        head = [1, 1, 2, 2, 1]
        seen = set(head)
        # 状态规模随去重后基数 (2) 而非块长 (5) 增长.
        self.assertEqual(seen, {1, 2})
        self.assertLess(len(seen), len(head))

    def test_with_key_function_history_stores_keys_not_elements(self):
        seq = ["A", "a", "B", "b", "A"]
        expected = list(unique_everseen(seq, key=str.casefold))

        head, tail = seq[:2], seq[2:]
        head_result = list(unique_everseen(head, key=str.casefold))
        seen_keys = {str.casefold(element) for element in head}
        tail_result = []
        for element in tail:
            key = str.casefold(element)
            if key not in seen_keys:
                seen_keys.add(key)
                tail_result.append(element)
        self.assertEqual(head_result + tail_result, expected)
        self.assertEqual(seen_keys, {"a", "b"})


class WholeResultSanityTests(TestCase):
    def test_reference_results(self):
        self.assertEqual(whole_map(DATA), [2, 2, 4, 4, 2, 6])
        self.assertEqual(
            whole_adjacent_groups(DATA), [[1, 1], [2, 2], [1], [3]]
        )
        self.assertEqual(
            whole_windows(DATA),
            [(1, 1, 2), (1, 2, 2), (2, 2, 1), (2, 1, 3)],
        )
        self.assertEqual(whole_dedup(DATA), [1, 2, 3])
        self.assertEqual(
            list(windowed(DATA, WINDOW_SIZE)), whole_windows(DATA)
        )
