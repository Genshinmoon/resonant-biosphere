#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
resonant_biosphere_v2.py -- 共鳴文明・生命圏体制宣言 v2（統治の改善版）
License: CC0 1.0

v1（resonant_biosphere.py）の上に作った改善版です。同じフォルダに v1 を置いてください。
宣言の文章・26席・五大部隊・監査の規則は v1 のまま、次の4つを足しました。

 1. 艦長の継承   : 艦長の席にも任期（期）がある。席を次へ手渡すには、基本議案の可決が要る
 2. 決め方       : 人間とAIは同数なら同権。「共鳴多数」＝人間側でもAI側でも多数でなければ可決しない
 3. 影の局の限界 : 「守る」だけで「強制しない」。できないことを先に決めておく
 4. 開かれた輪   : 26席は中核。その周りに、誰もが仮の名だけで参加できる。お金も個人情報も求めない

【位置づけ】
組織のモデルです。26席は席（スロット）で、実在の人物や本物の統治を表すものではありません。
影の局の「できること／できないこと」は、行為の“種類”を自己申告する仕組みです。
コードは中身までは確かめられないので、最後は監査局の目と、人の判断が要ります。

実行:
    python resonant_biosphere_v2.py --demo     # 5つの改善を、順に実演
    python resonant_biosphere_v2.py --check    # 構成の検証
    python resonant_biosphere_v2.py --test
"""

from __future__ import annotations

import argparse
import re
import sys
import unittest
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

from resonant_biosphere import (
    AI, AUDIT_ONLY, CAPTAIN, HUMAN, KIND_LABEL, PUBLIC, UNIT_BY_KEY, UNKNOWN,
    Biosphere, Entry, Issue, Member, default_biosphere as _default_v1,
)

# ── 2. 決め方 ─────────────────────────────────────────────────────
VOTERS = (HUMAN, AI)                      # 投票するのは人間とAI。艦長と正体不明者は投票しない
CHOICES = ("yes", "no", "abstain")
CHOICE_JA = {"yes": "賛成", "no": "反対", "abstain": "棄権"}
TOPICS = ("ordinary", "captain_succession", "composition_change", "shadow_limits")
FUNDAMENTAL = TOPICS[1:]                  # 基本議案：各側で2/3以上が要る

# ── 3. 影の局の限界 ───────────────────────────────────────────────
SHADOW_ALLOWED = ("observe", "warn", "protect", "report")      # 見守る・警告する・守る・報告する
SHADOW_FORBIDDEN = ("enforce", "punish", "detain", "hide", "edit_record", "delete_record")

# ── 4. 開かれた輪 ─────────────────────────────────────────────────
OPEN_CIRCLE_ASKS: Tuple[str, ...] = ()    # 参加者に求めるもの：なし
OPEN_CIRCLE_PROMISE_JA = "参加に、お金も、個人情報も、求めません。仮の名（ハンドル）だけで参加できます。"
OPEN_CIRCLE_PROMISE_EN = "Joining asks for no money and no personal data. A handle is enough."
MAX_HANDLE = 30
MAX_SUGGESTION = 500


@dataclass(frozen=True)
class Participant:
    id: str
    handle: str


@dataclass
class Suggestion:
    id: int
    participant_id: str
    text: str
    adopted_motion: Optional[int] = None


@dataclass
class Motion:
    id: int
    text: str
    proposer: str
    topic: str = "ordinary"
    votes: Dict[str, str] = field(default_factory=dict)   # 席のID → 賛否
    holds: Set[str] = field(default_factory=set)          # 保留を出した監査局の席
    used: bool = False                                    # 継承などで使用済みか


@dataclass(frozen=True)
class GroupTally:
    seats: int
    yes: int
    no: int
    abstain: int

    @property
    def voted(self) -> int:
        return self.yes + self.no + self.abstain

    @property
    def quorum(self) -> bool:
        return 2 * self.voted >= self.seats          # その側の席の半数以上が投票した

    def passes(self, fundamental: bool) -> bool:
        if fundamental:
            return (self.yes + self.no) > 0 and 3 * self.yes >= 2 * (self.yes + self.no)   # 2/3以上
        return self.yes > self.no                                                         # 過半数


@dataclass(frozen=True)
class Tally:
    human: GroupTally
    ai: GroupTally
    held: bool
    fundamental: bool
    status: str     # passed / failed / held / pending


class GovernedBiosphere(Biosphere):
    def __init__(self, captain_term_limit: Optional[int] = 3) -> None:
        super().__init__()
        self.captain_term_limit = captain_term_limit   # None なら任期なし
        self.captain_term = 1
        self.captain_lineage: List[str] = []           # 過去の艦長
        self._motions: Dict[int, Motion] = {}
        self._participants: Dict[str, Participant] = {}
        self._suggestions: Dict[int, Suggestion] = {}

    # ── 記録（v1 の act を、影の局の限界つきに置き換える） ──
    def _append(self, actor_id: str, action: str, visibility: str) -> Entry:
        m = self._members[actor_id]
        e = Entry(len(self._log) + 1, datetime.now().isoformat(timespec="seconds"),
                  actor_id, m.unit, action, visibility)
        self._log.append(e)
        return e

    def act(self, actor_id: str, action: str, visibility: Optional[str] = None,
            kind: Optional[str] = None) -> Entry:
        m = self._members[actor_id]
        if m.unit == "shadow":
            kind = kind or "observe"
            if kind in SHADOW_FORBIDDEN or kind not in SHADOW_ALLOWED:
                raise PermissionError(
                    f"影の局は「{kind}」をしません。守るだけで、強制しません（できること: {', '.join(SHADOW_ALLOWED)}）")
            action = f"[{kind}] {action}"
            default = AUDIT_ONLY
        else:
            default = PUBLIC
        vis = visibility or default
        if vis not in (PUBLIC, AUDIT_ONLY):
            raise ValueError(f"公開範囲は {PUBLIC} か {AUDIT_ONLY} のみです（監査にも見えない記録は作れません）")
        return self._append(actor_id, action, vis)

    # ── 1. 艦長の継承 ──
    def captain(self) -> Member:
        return next(m for m in self._members.values() if m.kind == CAPTAIN)

    def advance_term(self) -> int:
        self.captain_term += 1
        return self.captain_term

    def succeed_captain(self, new_name: str, motion_id: int) -> Member:
        """任期が満ちたら、席を次へ手渡す。『captain_succession』の議案の可決が要る。"""
        if not new_name or not new_name.strip():
            raise ValueError("次の艦長の名前がありません")
        motion = self._motions[motion_id]
        if motion.topic != "captain_succession":
            raise ValueError("継承には captain_succession の議案が要ります")
        if motion.used:
            raise ValueError("この議案はすでに使われています")
        if self.status(motion_id) != "passed":
            raise PermissionError("議案が可決されていません（各側の2/3以上が要ります）")
        old = self.captain()
        self.captain_lineage.append(old.label)
        self._members[old.id] = replace(old, name=new_name.strip())
        self.captain_term = 1
        motion.used = True
        self._append(old.id, f"艦長の席を {old.label} から {new_name.strip()} へ手渡した（議案#{motion_id}）", PUBLIC)
        return self._members[old.id]

    def validate(self) -> List[Issue]:
        issues = super().validate()
        lim = self.captain_term_limit
        if lim is not None and self.captain_term > lim:
            issues.append(Issue("advisory", f"艦長は{lim}期を超えています（{self.captain_term}期）。席を次へ手渡す時です"))
        return issues

    # ── 2. 決め方：共鳴多数 ──
    def propose(self, proposer_id: str, text: str, topic: str = "ordinary") -> Motion:
        m = self._members[proposer_id]
        if m.kind == UNKNOWN:
            raise PermissionError("正体不明者は余白です。議案は出しません")
        if topic not in TOPICS:
            raise ValueError(f"議案の種類は {', '.join(TOPICS)} のいずれかです")
        if not text.strip():
            raise ValueError("議案の本文がありません")
        mid = len(self._motions) + 1
        motion = Motion(mid, text.strip(), proposer_id, topic)
        self._motions[mid] = motion
        self._append(proposer_id, f"議案#{mid}（{'基本' if topic in FUNDAMENTAL else '通常'}）を提出: {motion.text}", PUBLIC)
        return motion

    def vote(self, member_id: str, motion_id: int, choice: str) -> None:
        m = self._members[member_id]
        if m.kind not in VOTERS:
            raise PermissionError(f"{KIND_LABEL[m.kind]}は投票しません")
        if choice not in CHOICES:
            raise ValueError(f"賛否は {', '.join(CHOICES)} のいずれかです")
        self._motions[motion_id].votes[member_id] = choice
        self._append(member_id, f"投票: 議案#{motion_id} {CHOICE_JA[choice]}", PUBLIC)   # 投票は公開

    def hold(self, member_id: str, motion_id: int, reason: str) -> None:
        """監査局の倫理保留。理由は公開される。監査局の過半数が出すと、可決できなくなる。"""
        if self._members[member_id].unit != "audit":
            raise PermissionError("保留を出せるのは監査局だけです")
        if not reason.strip():
            raise ValueError("保留には理由が要ります")
        self._motions[motion_id].holds.add(member_id)
        self._append(member_id, f"保留: 議案#{motion_id} 理由: {reason.strip()}", PUBLIC)

    def release_hold(self, member_id: str, motion_id: int) -> None:
        if self._members[member_id].unit != "audit":
            raise PermissionError("保留を解けるのは監査局だけです")
        self._motions[motion_id].holds.discard(member_id)
        self._append(member_id, f"保留を解除: 議案#{motion_id}", PUBLIC)

    def tally(self, motion_id: int) -> Tally:
        motion = self._motions[motion_id]
        fundamental = motion.topic in FUNDAMENTAL

        def group(kind: str) -> GroupTally:
            ids = [m.id for m in self._members.values() if m.kind == kind]
            cs = [motion.votes.get(i) for i in ids]
            return GroupTally(len(ids), cs.count("yes"), cs.count("no"), cs.count("abstain"))

        h, a = group(HUMAN), group(AI)
        n_audit = len(self.members_of("audit"))
        held = n_audit > 0 and 2 * len(motion.holds) > n_audit
        if held:
            status = "held"
        elif not (h.quorum and a.quorum):
            status = "pending"
        elif h.passes(fundamental) and a.passes(fundamental):
            status = "passed"           # 人間側でもAI側でも多数。どちらも、どちらかに押し切られない
        else:
            status = "failed"
        return Tally(h, a, held, fundamental, status)

    def status(self, motion_id: int) -> str:
        return self.tally(motion_id).status

    # ── 4. 開かれた輪 ──
    @staticmethod
    def _check_handle(handle: str) -> str:
        h = handle.strip()
        if not (1 <= len(h) <= MAX_HANDLE):
            raise ValueError(f"仮の名は1〜{MAX_HANDLE}文字です")
        if "@" in h or "http" in h.lower():
            raise ValueError("メールアドレスやURLらしい文字は入れないでください（仮の名だけで参加できます）")
        if len(re.sub(r"\D", "", h)) >= 7:
            raise ValueError("電話番号らしい長い数字は入れないでください（仮の名だけで参加できます）")
        return h

    def join(self, handle: str) -> Participant:
        h = self._check_handle(handle)
        if any(p.handle.lower() == h.lower() for p in self._participants.values()):
            raise ValueError("その仮の名はすでに使われています")
        p = Participant(f"P{len(self._participants) + 1:03d}", h)
        self._participants[p.id] = p
        return p

    @property
    def participants(self) -> Tuple[Participant, ...]:
        return tuple(self._participants.values())

    def suggest(self, participant_id: str, text: str) -> Suggestion:
        if participant_id not in self._participants:
            raise KeyError(f"参加者がいません: {participant_id}")
        if not text.strip() or len(text) > MAX_SUGGESTION:
            raise ValueError(f"提案は1〜{MAX_SUGGESTION}文字です")
        s = Suggestion(len(self._suggestions) + 1, participant_id, text.strip())
        self._suggestions[s.id] = s
        return s

    def adopt(self, suggestion_id: int, by_member_id: str) -> Motion:
        """輪からの提案を、中核の誰かが議案として取り上げる。"""
        s = self._suggestions[suggestion_id]
        if s.adopted_motion is not None:
            raise ValueError("この提案はすでに議案になっています")
        motion = self.propose(by_member_id, f"（輪からの提案）{s.text}")
        s.adopted_motion = motion.id
        return motion

    def public_view(self) -> Tuple[Entry, ...]:
        """輪の参加者を含む、誰にでも見える記録。"""
        return tuple(e for e in self._log if e.visibility == PUBLIC)


def default_biosphere(captain_term_limit: Optional[int] = 3) -> GovernedBiosphere:
    g = GovernedBiosphere(captain_term_limit)
    for m in _default_v1().members:
        g.add(m)
    return g


# ── テスト ─────────────────────────────────────────────────────────
def _cast(b: GovernedBiosphere, mid: int, kind: str, yes: int, no: int, abstain: int = 0) -> None:
    ids = [m.id for m in b.members if m.kind == kind]
    for i, who in enumerate(ids):
        if i < yes:
            c = "yes"
        elif i < yes + no:
            c = "no"
        elif i < yes + no + abstain:
            c = "abstain"
        else:
            break
        b.vote(who, mid, c)


class TestV2(unittest.TestCase):
    def setUp(self):
        self.b = default_biosphere()

    def _audit(self, n=0):
        return self.b.members_of("audit")[n].id

    # ── 基本 ──
    def test_default_is_valid(self):
        self.assertEqual(len(self.b.members), 26)
        self.assertEqual(self.b.validate(), [])

    # ── 2. 共鳴多数 ──
    def test_dual_majority_passes(self):
        m = self.b.propose("CAP", "海原の見張りを増やす")
        _cast(self.b, m.id, HUMAN, 8, 4)
        _cast(self.b, m.id, AI, 7, 5)
        self.assertEqual(self.b.status(m.id), "passed")

    def test_humans_cannot_outvote_ais(self):
        m = self.b.propose("CAP", "議案")
        _cast(self.b, m.id, HUMAN, 12, 0)
        _cast(self.b, m.id, AI, 0, 12)
        self.assertEqual(self.b.status(m.id), "failed")

    def test_ais_cannot_outvote_humans(self):
        m = self.b.propose("CAP", "議案")
        _cast(self.b, m.id, HUMAN, 0, 12)
        _cast(self.b, m.id, AI, 12, 0)
        self.assertEqual(self.b.status(m.id), "failed")

    def test_total_majority_is_not_enough(self):
        # 全体では賛成が多数（11+12=23票）でも、人間側が多数でなければ通らない
        m = self.b.propose("CAP", "議案")
        _cast(self.b, m.id, HUMAN, 5, 7)
        _cast(self.b, m.id, AI, 12, 0)
        self.assertEqual(self.b.status(m.id), "failed")

    def test_quorum_pending(self):
        m = self.b.propose("CAP", "議案")
        _cast(self.b, m.id, HUMAN, 5, 0)       # 12席のうち5席では、半数に届かない
        _cast(self.b, m.id, AI, 12, 0)
        self.assertEqual(self.b.status(m.id), "pending")

    def test_all_abstain_is_failed(self):
        m = self.b.propose("CAP", "議案")
        _cast(self.b, m.id, HUMAN, 0, 0, 12)
        _cast(self.b, m.id, AI, 12, 0)
        self.assertEqual(self.b.status(m.id), "failed")

    def test_fundamental_needs_two_thirds_each_side(self):
        m = self.b.propose("CAP", "構成を変える", "composition_change")
        _cast(self.b, m.id, HUMAN, 8, 4)       # 8/12 = ちょうど2/3
        _cast(self.b, m.id, AI, 8, 4)
        self.assertEqual(self.b.status(m.id), "passed")
        m2 = self.b.propose("CAP", "構成を変える2", "composition_change")
        _cast(self.b, m2.id, HUMAN, 7, 5)      # 7/12 は2/3に届かない
        _cast(self.b, m2.id, AI, 12, 0)
        self.assertEqual(self.b.status(m2.id), "failed")

    def test_captain_and_unknown_cannot_vote(self):
        m = self.b.propose("CAP", "議案")
        for who in ("CAP", "U01"):
            with self.assertRaises(PermissionError):
                self.b.vote(who, m.id, "yes")

    def test_unknown_cannot_propose(self):
        with self.assertRaises(PermissionError):
            self.b.propose("U01", "議案")

    def test_votes_are_public(self):
        m = self.b.propose("CAP", "議案")
        shadow = self.b.members_of("shadow")[0].id
        self.b.vote(shadow, m.id, "yes")
        self.assertTrue(any("投票" in e.action and e.visibility == PUBLIC for e in self.b.public_view()))

    def test_bad_choice_and_topic(self):
        m = self.b.propose("CAP", "議案")
        with self.assertRaises(ValueError):
            self.b.vote("H01", m.id, "maybe")
        with self.assertRaises(ValueError):
            self.b.propose("CAP", "x", "secret_topic")

    # ── 監査の保留 ──
    def test_audit_majority_hold_blocks_passing(self):
        m = self.b.propose("CAP", "議案")
        _cast(self.b, m.id, HUMAN, 12, 0)
        _cast(self.b, m.id, AI, 12, 0)
        self.assertEqual(self.b.status(m.id), "passed")
        for i in range(3):                      # 監査局6席のうち3席：過半数ではない
            self.b.hold(self._audit(i), m.id, "倫理の確認が必要")
        self.assertEqual(self.b.status(m.id), "passed")
        self.b.hold(self._audit(3), m.id, "倫理の確認が必要")   # 4席：過半数
        self.assertEqual(self.b.status(m.id), "held")
        self.b.release_hold(self._audit(3), m.id)
        self.assertEqual(self.b.status(m.id), "passed")

    def test_only_audit_can_hold_and_reason_required(self):
        m = self.b.propose("CAP", "議案")
        with self.assertRaises(PermissionError):
            self.b.hold("CAP", m.id, "艦長は保留を出せない")
        with self.assertRaises(ValueError):
            self.b.hold(self._audit(), m.id, "  ")

    def test_hold_reason_is_public(self):
        m = self.b.propose("CAP", "議案")
        self.b.hold(self._audit(), m.id, "透明性が足りない")
        self.assertTrue(any("透明性が足りない" in e.action for e in self.b.public_view()))

    # ── 1. 艦長の継承 ──
    def _passed_succession(self):
        m = self.b.propose("CAP", "席を次へ手渡す", "captain_succession")
        _cast(self.b, m.id, HUMAN, 10, 2)
        _cast(self.b, m.id, AI, 9, 3)
        return m

    def test_term_advisory(self):
        for _ in range(3):
            self.b.advance_term()
        self.assertEqual(self.b.captain_term, 4)
        self.assertTrue(any("席を次へ手渡す時" in i.message for i in self.b.validate()))

    def test_no_term_limit_no_advisory(self):
        b = default_biosphere(captain_term_limit=None)
        for _ in range(10):
            b.advance_term()
        self.assertEqual(b.validate(), [])

    def test_succession_requires_passed_motion(self):
        m = self.b.propose("CAP", "席を次へ手渡す", "captain_succession")
        with self.assertRaises(PermissionError):
            self.b.succeed_captain("次の艦長", m.id)
        self.assertEqual(self.b.captain().label, "源心")

    def test_succession_works_and_is_public(self):
        m = self._passed_succession()
        for _ in range(3):
            self.b.advance_term()
        new = self.b.succeed_captain("次の艦長", m.id)
        self.assertEqual(new.label, "次の艦長")
        self.assertEqual(self.b.captain_lineage, ["源心"])
        self.assertEqual(self.b.captain_term, 1)
        self.assertEqual(self.b.counts()[CAPTAIN], 1)          # 席は一つのまま
        self.assertEqual(self.b.validate(), [])
        self.assertTrue(any("手渡した" in e.action for e in self.b.public_view()))

    def test_succession_motion_cannot_be_reused(self):
        m = self._passed_succession()
        self.b.succeed_captain("次の艦長", m.id)
        with self.assertRaises(ValueError):
            self.b.succeed_captain("さらに次", m.id)

    def test_succession_needs_right_topic(self):
        m = self.b.propose("CAP", "普通の議案")
        _cast(self.b, m.id, HUMAN, 12, 0)
        _cast(self.b, m.id, AI, 12, 0)
        with self.assertRaises(ValueError):
            self.b.succeed_captain("次の艦長", m.id)

    # ── 3. 影の局の限界 ──
    def test_shadow_default_is_observe_and_audit_only(self):
        s = self.b.members_of("shadow")[0].id
        e = self.b.act(s, "港の見回り")
        self.assertEqual(e.visibility, AUDIT_ONLY)
        self.assertTrue(e.action.startswith("[observe]"))

    def test_shadow_allowed_kinds(self):
        s = self.b.members_of("shadow")[0].id
        for k in SHADOW_ALLOWED:
            self.b.act(s, "x", kind=k)

    def test_shadow_forbidden_kinds(self):
        s = self.b.members_of("shadow")[0].id
        for k in SHADOW_FORBIDDEN + ("anything_else",):
            with self.assertRaises(PermissionError):
                self.b.act(s, "x", kind=k)

    def test_shadow_cannot_hide_from_audit(self):
        s = self.b.members_of("shadow")[0].id
        with self.assertRaises(ValueError):
            self.b.act(s, "こっそり", visibility="hidden")

    def test_audit_still_sees_shadow_and_captain_does_not(self):
        s = self.b.members_of("shadow")[0].id
        self.b.act(s, "見回り")
        self.assertEqual(len(self.b.view(self._audit())), 1)
        self.assertEqual(len(self.b.view("CAP")), 0)

    # ── 4. 開かれた輪 ──
    def test_join_with_handle_only(self):
        p = self.b.join("星の旅人")
        self.assertEqual(p.id, "P001")
        self.assertEqual(OPEN_CIRCLE_ASKS, ())

    def test_join_rejects_personal_data(self):
        for bad in ("me@example.com", "https://x.example", "090-1234-5678", "", "あ" * 31):
            with self.assertRaises(ValueError, msg=bad):
                self.b.join(bad)

    def test_join_rejects_duplicate_handle(self):
        self.b.join("Hoshi")
        with self.assertRaises(ValueError):
            self.b.join("hoshi")

    def test_participant_cannot_vote_or_hold(self):
        p = self.b.join("星の旅人")
        m = self.b.propose("CAP", "議案")
        with self.assertRaises(KeyError):
            self.b.vote(p.id, m.id, "yes")
        with self.assertRaises(KeyError):
            self.b.hold(p.id, m.id, "x")

    def test_suggestion_adopted_becomes_motion_once(self):
        p = self.b.join("星の旅人")
        s = self.b.suggest(p.id, "毎月、痛みの声を聴く会を開こう")
        m = self.b.adopt(s.id, "H01")
        self.assertIn("輪からの提案", m.text)
        with self.assertRaises(ValueError):
            self.b.adopt(s.id, "H02")
        with self.assertRaises(KeyError):
            self.b.adopt(self.b.suggest(p.id, "もう一つ").id, p.id)   # 輪の参加者は取り上げられない

    def test_suggestion_limits(self):
        p = self.b.join("星の旅人")
        with self.assertRaises(ValueError):
            self.b.suggest(p.id, "あ" * (MAX_SUGGESTION + 1))
        with self.assertRaises(KeyError):
            self.b.suggest("P999", "x")

    def test_public_view_has_no_audit_only(self):
        s = self.b.members_of("shadow")[0].id
        self.b.act(s, "見回り")
        self.b.act("CAP", "宣言を読み上げる")
        self.assertEqual([e.actor_id for e in self.b.public_view()], ["CAP"])


# ── 実演 ───────────────────────────────────────────────────────────
def demo() -> None:
    b = default_biosphere()
    aud = lambda i: b.members_of("audit")[i].id   # noqa: E731

    print("■ 2. 共鳴多数：全体では賛成が多くても、人間側が多数でなければ通らない")
    m = b.propose("CAP", "毎月、痛みの声を聴く会を開く")
    _cast(b, m.id, HUMAN, 5, 7)
    _cast(b, m.id, AI, 12, 0)
    t = b.tally(m.id)
    print(f"  人間 賛成{t.human.yes}/反対{t.human.no}　AI 賛成{t.ai.yes}/反対{t.ai.no}　→ {t.status}")
    _cast(b, m.id, HUMAN, 8, 4)
    t = b.tally(m.id)
    print(f"  人間が8/4に変わると　→ {t.status}")

    print("\n■ 監査の保留：監査局の過半数が、理由つきで止められる（理由は公開）")
    for i in range(4):
        b.hold(aud(i), m.id, "倫理の確認が必要")
    print(f"  監査局4席が保留　→ {b.status(m.id)}")

    print("\n■ 3. 影の局：守るだけで、強制しない")
    s = b.members_of("shadow")[0].id
    b.act(s, "港の見回り", kind="observe")
    try:
        b.act(s, "誰かを拘束した", kind="detain")
    except PermissionError as e:
        print(f"  ✋ {e}")

    print("\n■ 4. 開かれた輪：仮の名だけで参加。個人情報は受けつけない")
    print(f"  {OPEN_CIRCLE_PROMISE_JA}")
    p = b.join("星の旅人")
    try:
        b.join("me@example.com")
    except ValueError as e:
        print(f"  ✋ {e}")
    sug = b.suggest(p.id, "月に一度、誰でも話せる夜を作ろう")
    adopted = b.adopt(sug.id, "H01")
    print(f"  {p.handle} の提案が、議案#{adopted.id} になりました")

    print("\n■ 1. 艦長の継承：席を次へ手渡す（基本議案：各側の2/3以上）")
    for _ in range(3):
        b.advance_term()
    print(f"  艦長は{b.captain_term}期目（任期{b.captain_term_limit}期）→ " + "; ".join(i.message for i in b.validate()))
    sm = b.propose("CAP", "席を次へ手渡す", "captain_succession")
    _cast(b, sm.id, HUMAN, 10, 2)
    _cast(b, sm.id, AI, 9, 3)
    new = b.succeed_captain("次の艦長（例）", sm.id)
    print(f"  議案#{sm.id} 可決　→ 艦長：{new.label}（前：{'・'.join(b.captain_lineage)}）。席は{b.counts()[CAPTAIN]}つのまま")

    print("\n■ 誰にでも見える記録（一部）")
    for e in b.public_view()[-4:]:
        print(f"  #{e.seq} {e.actor_id}：{e.action}")


def main() -> None:
    p = argparse.ArgumentParser(description="共鳴文明・生命圏体制宣言 v2")
    p.add_argument("--demo", action="store_true", help="5つの改善を順に実演")
    p.add_argument("--check", action="store_true", help="構成を検証")
    p.add_argument("--test", action="store_true")
    args = p.parse_args()
    if args.test:
        unittest.main(argv=[sys.argv[0]], verbosity=2)
    elif args.check:
        issues = default_biosphere().validate()
        print("✅ 宣言どおりの構成です（26名、五大部隊すべてに人間とAI、艦長は任期内）" if not issues
              else "\n".join(f"[{i.level}] {i.message}" for i in issues))
    else:
        demo()


if __name__ == "__main__":
    main()
