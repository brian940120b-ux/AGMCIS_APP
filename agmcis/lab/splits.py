"""
資料切分。

所有切分都是**時間序列切分**,不是隨機切分。

金融資料隨機打散再切 train/test 是典型的資料洩漏:
測試集裡會出現訓練集後面的 K 棒,而相鄰 K 棒高度相關,
模型等於間接看過答案。這種驗證出來的結果一定好看,而且一定是假的。
"""
from dataclasses import dataclass
from typing import List


@dataclass
class Split:
    """一組 (訓練, 測試) 的索引範圍。半開區間 [start, end)。"""
    train_start: int
    train_end: int
    test_start: int
    test_end: int
    label: str = ""

    @property
    def train_size(self):
        return self.train_end - self.train_start

    @property
    def test_size(self):
        return self.test_end - self.test_start

    def train_slice(self, rows):
        return rows[self.train_start:self.train_end]

    def test_slice(self, rows):
        return rows[self.test_start:self.test_end]

    def to_dict(self):
        return {
            "label": self.label,
            "train": [self.train_start, self.train_end],
            "test": [self.test_start, self.test_end],
            "train_size": self.train_size,
            "test_size": self.test_size,
        }


def in_sample_out_of_sample(total, oos_ratio=0.3):
    """
    單次切分:前面 (1 - oos_ratio) 調參,後面 oos_ratio 驗證。

    **測試段一定在訓練段之後。** 反過來(用未來調參、在過去驗證)
    是前視偏誤,而且看起來會很合理,因為兩段資料都是「真實歷史」。
    """
    if total <= 0:
        raise ValueError("資料長度必須大於 0")
    if not 0 < oos_ratio < 1:
        raise ValueError("oos_ratio 必須介於 0 與 1 之間")

    split_at = int(total * (1 - oos_ratio))

    if split_at <= 0 or split_at >= total:
        raise ValueError(f"資料只有 {total} 根,無法用 {oos_ratio} 切出有意義的 OOS")

    return Split(
        train_start=0, train_end=split_at,
        test_start=split_at, test_end=total,
        label="IS/OOS",
    )


def walk_forward(total, train_size, test_size, step=None, anchored=False):
    """
    滾動切分。

    anchored=False(預設,rolling):訓練視窗固定長度往前滾。
        市場結構會變,五年前的參數對今天不一定有意義。
    anchored=True(expanding):訓練視窗起點固定,越滾越長。

    每一段的測試資料都**緊接在**自己的訓練資料之後,而且
    **從不重複使用**(step 預設等於 test_size)。
    測試段重疊會讓同一段資料被計入多次,把樣本數灌水。
    """
    if train_size <= 0 or test_size <= 0:
        raise ValueError("train_size 與 test_size 必須大於 0")

    step = test_size if step is None else step
    if step <= 0:
        raise ValueError("step 必須大於 0")

    splits: List[Split] = []
    train_start = 0

    while True:
        train_end = train_start + train_size
        test_end = train_end + test_size

        if test_end > total:
            break

        splits.append(Split(
            train_start=0 if anchored else train_start,
            train_end=train_end,
            test_start=train_end,
            test_end=test_end,
            label=f"WF{len(splits) + 1}",
        ))

        train_start += step

    if not splits:
        raise ValueError(
            f"資料只有 {total} 根,放不下一段 "
            f"train={train_size} + test={test_size} 的視窗"
        )

    return splits
