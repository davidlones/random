"""
Observable 5D Chess — “all of the above” edition
================================================

Implements (as requested):
✅ Universal moves across the observable cone (one move applied everywhere it’s legal)
✅ Timelines that can’t accept the universal move become inaccessible (frozen)
✅ Observable cone: only X timelines are visible at a time; sampled with past/future bias
✅ “Time travel” = re-centering your observable cone of awareness
✅ A real adjacency graph of realities (past/future neighbors), built lazily
✅ A temporal move type: re-center ONLY to an adjacent reality where a chosen piece
   already exists on the chosen square (approx identity check: same color+kind on square)
✅ Full chess rules including:
   - castling (with check/through-check rules)
   - en passant
   - promotion
(Still intentionally omits: 50-move rule, threefold repetition adjudication, draw claims.)

CLI commands:
  show
  move e2e4
  move e7e8q
  recenter past|future|mix
  tcenter e4 past      # re-center to an adjacent timeline where your piece already sits on e4
  expand               # force-generate more adjacent futures from visible timelines
  help
  quit

Run:
  python observable_5d_chess.py
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Tuple, Optional, Dict, Set, Iterable
import random
import copy

FILES = "abcdefgh"
RANKS = "12345678"

WHITE, BLACK = "w", "b"
PIECE_TYPES = {"p", "n", "b", "r", "q", "k"}

# ---------------------------- Helpers ----------------------------------------

def uci_to_sq(s: str) -> Tuple[int, int]:
    file = FILES.index(s[0])
    rank = RANKS.index(s[1])  # 0 for '1'
    row = 7 - rank
    col = file
    return (row, col)

def sq_to_uci(sq: Tuple[int, int]) -> str:
    row, col = sq
    file = FILES[col]
    rank = RANKS[7 - row]
    return file + rank

@dataclass(frozen=True)
class Piece:
    color: str  # 'w' or 'b'
    kind: str   # p n b r q k

    def __str__(self) -> str:
        return self.kind.upper() if self.color == WHITE else self.kind.lower()

@dataclass(frozen=True)
class Move:
    fr: Tuple[int, int]
    to: Tuple[int, int]
    promo: Optional[str] = None   # q r b n
    castle: Optional[str] = None  # "K","Q","k","q"
    en_passant: bool = False

    def uci(self) -> str:
        base = sq_to_uci(self.fr) + sq_to_uci(self.to)
        if self.promo:
            base += self.promo
        return base

def parse_move_uci(token: str) -> Move:
    token = token.strip().lower()
    if len(token) not in (4, 5):
        raise ValueError("Move must be like e2e4 or e7e8q")
    fr = uci_to_sq(token[0:2])
    to = uci_to_sq(token[2:4])
    promo = token[4] if len(token) == 5 else None
    if promo is not None and promo not in ("q", "r", "b", "n"):
        raise ValueError("Promotion must be one of q,r,b,n")
    return Move(fr, to, promo=promo)

# ---------------------------- Chess Board ------------------------------------

@dataclass
class Board:
    grid: List[List[Optional[Piece]]] = field(default_factory=lambda: [[None]*8 for _ in range(8)])
    turn: str = WHITE
    castling: str = "KQkq"          # castling rights
    ep_target: Optional[Tuple[int,int]] = None  # en passant target square (the square the pawn passes over)
    halfmove: int = 0
    fullmove: int = 1

    @staticmethod
    def starting() -> "Board":
        b = Board()
        for c in range(8):
            b.grid[6][c] = Piece(WHITE, "p")
            b.grid[1][c] = Piece(BLACK, "p")
        back = ["r","n","b","q","k","b","n","r"]
        for c, k in enumerate(back):
            b.grid[7][c] = Piece(WHITE, k)
            b.grid[0][c] = Piece(BLACK, k)
        b.turn = WHITE
        b.castling = "KQkq"
        b.ep_target = None
        b.halfmove = 0
        b.fullmove = 1
        return b

    def clone(self) -> "Board":
        return copy.deepcopy(self)

    def in_bounds(self, r: int, c: int) -> bool:
        return 0 <= r < 8 and 0 <= c < 8

    def piece_at(self, sq: Tuple[int,int]) -> Optional[Piece]:
        r,c = sq
        return self.grid[r][c]

    def set_piece(self, sq: Tuple[int,int], p: Optional[Piece]) -> None:
        r,c = sq
        self.grid[r][c] = p

    def find_king(self, color: str) -> Tuple[int,int]:
        for r in range(8):
            for c in range(8):
                p = self.grid[r][c]
                if p and p.color == color and p.kind == "k":
                    return (r,c)
        raise RuntimeError("King missing (illegal position)")

    def signature(self) -> str:
        rows = []
        for r in range(8):
            s = ""
            for c in range(8):
                p = self.grid[r][c]
                s += (str(p) if p else ".")
            rows.append(s)
        ep = sq_to_uci(self.ep_target) if self.ep_target else "-"
        return "/".join(rows) + f" {self.turn} {''.join(sorted(self.castling)) or '-'} {ep}"

    def ascii(self) -> str:
        lines = []
        for r in range(8):
            row = []
            for c in range(8):
                p = self.grid[r][c]
                row.append(str(p) if p else ".")
            lines.append(f"{8-r} " + " ".join(row))
        lines.append("  " + " ".join(FILES))
        ep = sq_to_uci(self.ep_target) if self.ep_target else "-"
        lines.append(f"turn: {self.turn} | castling: {''.join(sorted(self.castling)) or '-'} | ep: {ep}")
        return "\n".join(lines)

    # ----------------------- Attacks / Checks --------------------------------

    def is_attacked_by(self, target: Tuple[int,int], attacker_color: str) -> bool:
        tr, tc = target
        opp = attacker_color

        # pawns
        dir_ = -1 if opp == WHITE else 1
        for dc in (-1, 1):
            rr, cc = tr + (-dir_), tc + dc  # reverse the pawn move
            if self.in_bounds(rr, cc):
                p = self.grid[rr][cc]
                if p and p.color == opp and p.kind == "p":
                    return True

        # knights
        for dr,dc in [(-2,-1),(-2,1),(-1,-2),(-1,2),(1,-2),(1,2),(2,-1),(2,1)]:
            rr, cc = tr+dr, tc+dc
            if self.in_bounds(rr, cc):
                p = self.grid[rr][cc]
                if p and p.color == opp and p.kind == "n":
                    return True

        # bishops/queens diagonals
        for dr,dc in [(-1,-1),(-1,1),(1,-1),(1,1)]:
            rr, cc = tr+dr, tc+dc
            while self.in_bounds(rr,cc):
                p = self.grid[rr][cc]
                if p:
                    if p.color == opp and p.kind in ("b","q"):
                        return True
                    break
                rr += dr
                cc += dc

        # rooks/queens orthogonals
        for dr,dc in [(-1,0),(1,0),(0,-1),(0,1)]:
            rr, cc = tr+dr, tc+dc
            while self.in_bounds(rr,cc):
                p = self.grid[rr][cc]
                if p:
                    if p.color == opp and p.kind in ("r","q"):
                        return True
                    break
                rr += dr
                cc += dc

        # king
        for dr in (-1,0,1):
            for dc in (-1,0,1):
                if dr==0 and dc==0: continue
                rr, cc = tr+dr, tc+dc
                if self.in_bounds(rr,cc):
                    p = self.grid[rr][cc]
                    if p and p.color == opp and p.kind == "k":
                        return True

        return False

    def in_check(self, color: str) -> bool:
        ksq = self.find_king(color)
        opp = WHITE if color == BLACK else BLACK
        return self.is_attacked_by(ksq, opp)

    # ----------------------- Move Generation ---------------------------------

    def pseudo_moves_from(self, fr: Tuple[int,int]) -> List[Move]:
        r,c = fr
        p = self.piece_at(fr)
        if not p:
            return []
        color = p.color
        opp = WHITE if color == BLACK else BLACK
        moves: List[Move] = []

        def add_step(to_r: int, to_c: int):
            if not self.in_bounds(to_r,to_c):
                return
            t = self.grid[to_r][to_c]
            if t is None or t.color == opp:
                moves.append(Move(fr, (to_r,to_c)))

        if p.kind == "p":
            dir_ = -1 if color == WHITE else 1
            start_row = 6 if color == WHITE else 1
            promo_row = 0 if color == WHITE else 7

            # forward 1
            f1 = (r+dir_, c)
            if self.in_bounds(*f1) and self.grid[f1[0]][f1[1]] is None:
                if f1[0] == promo_row:
                    for pr in ("q","r","b","n"):
                        moves.append(Move(fr, f1, promo=pr))
                else:
                    moves.append(Move(fr, f1))
                # forward 2
                f2 = (r+2*dir_, c)
                if r == start_row and self.in_bounds(*f2) and self.grid[f2[0]][f2[1]] is None:
                    moves.append(Move(fr, f2))

            # captures
            for dc in (-1,1):
                cap = (r+dir_, c+dc)
                if not self.in_bounds(*cap):
                    continue
                t = self.grid[cap[0]][cap[1]]
                if t and t.color == opp:
                    if cap[0] == promo_row:
                        for pr in ("q","r","b","n"):
                            moves.append(Move(fr, cap, promo=pr))
                    else:
                        moves.append(Move(fr, cap))

            # en passant
            if self.ep_target is not None:
                epr, epc = self.ep_target
                if epr == r+dir_ and abs(epc - c) == 1:
                    # target square is empty; captured pawn is behind it
                    moves.append(Move(fr, (epr, epc), en_passant=True))
            return moves

        if p.kind == "n":
            for dr,dc in [(-2,-1),(-2,1),(-1,-2),(-1,2),(1,-2),(1,2),(2,-1),(2,1)]:
                add_step(r+dr, c+dc)
            return moves

        if p.kind in ("b","r","q"):
            dirs: List[Tuple[int,int]] = []
            if p.kind in ("b","q"):
                dirs += [(-1,-1),(-1,1),(1,-1),(1,1)]
            if p.kind in ("r","q"):
                dirs += [(-1,0),(1,0),(0,-1),(0,1)]
            for dr,dc in dirs:
                rr,cc = r+dr, c+dc
                while self.in_bounds(rr,cc):
                    t = self.grid[rr][cc]
                    if t is None:
                        moves.append(Move(fr,(rr,cc)))
                    else:
                        if t.color == opp:
                            moves.append(Move(fr,(rr,cc)))
                        break
                    rr += dr
                    cc += dc
            return moves

        if p.kind == "k":
            for dr in (-1,0,1):
                for dc in (-1,0,1):
                    if dr==0 and dc==0: continue
                    add_step(r+dr, c+dc)

            # castling
            if color == WHITE:
                ksq = (7,4)
                if fr == ksq and not self.in_check(WHITE):
                    if "K" in self.castling:
                        # squares f1,g1 empty and not attacked
                        if self.grid[7][5] is None and self.grid[7][6] is None:
                            if (not self.is_attacked_by((7,5), BLACK)) and (not self.is_attacked_by((7,6), BLACK)):
                                moves.append(Move(fr, (7,6), castle="K"))
                    if "Q" in self.castling:
                        if self.grid[7][3] is None and self.grid[7][2] is None and self.grid[7][1] is None:
                            if (not self.is_attacked_by((7,3), BLACK)) and (not self.is_attacked_by((7,2), BLACK)):
                                moves.append(Move(fr, (7,2), castle="Q"))
            else:
                ksq = (0,4)
                if fr == ksq and not self.in_check(BLACK):
                    if "k" in self.castling:
                        if self.grid[0][5] is None and self.grid[0][6] is None:
                            if (not self.is_attacked_by((0,5), WHITE)) and (not self.is_attacked_by((0,6), WHITE)):
                                moves.append(Move(fr, (0,6), castle="k"))
                    if "q" in self.castling:
                        if self.grid[0][3] is None and self.grid[0][2] is None and self.grid[0][1] is None:
                            if (not self.is_attacked_by((0,3), WHITE)) and (not self.is_attacked_by((0,2), WHITE)):
                                moves.append(Move(fr, (0,2), castle="q"))
            return moves

        return moves

    def legal_moves(self) -> List[Move]:
        color = self.turn
        mvs: List[Move] = []
        for r in range(8):
            for c in range(8):
                p = self.grid[r][c]
                if not p or p.color != color:
                    continue
                for mv in self.pseudo_moves_from((r,c)):
                    b2 = self.clone()
                    try:
                        b2.push(mv)
                    except Exception:
                        continue
                    if not b2.in_check(color):
                        mvs.append(mv)
        return mvs

    def is_legal(self, mv: Move) -> bool:
        return any(m.uci() == mv.uci() for m in self.legal_moves())

    # ----------------------- Apply Move --------------------------------------

    def _remove_castling_rights_for(self, color: str, side: str) -> None:
        # side is "K" or "Q" for white, "k" or "q" for black
        self.castling = "".join(ch for ch in self.castling if ch != side)

    def push(self, mv: Move) -> None:
        p = self.piece_at(mv.fr)
        if not p:
            raise ValueError("No piece at from-square")
        if p.color != self.turn:
            raise ValueError("Not that side's turn")

        # reset ep by default; may be set by pawn double move
        old_ep = self.ep_target
        self.ep_target = None

        # halfmove clock
        capture = self.piece_at(mv.to) is not None

        # castling move handling
        if mv.castle:
            # move king
            self.set_piece(mv.fr, None)
            self.set_piece(mv.to, p)
            # move rook accordingly
            if mv.castle in ("K","k"):  # king side
                rook_from = (7,7) if p.color == WHITE else (0,7)
                rook_to   = (7,5) if p.color == WHITE else (0,5)
            else:  # queen side
                rook_from = (7,0) if p.color == WHITE else (0,0)
                rook_to   = (7,3) if p.color == WHITE else (0,3)
            rook = self.piece_at(rook_from)
            if not rook or rook.kind != "r" or rook.color != p.color:
                raise ValueError("Invalid castling rook")
            self.set_piece(rook_from, None)
            self.set_piece(rook_to, rook)
            # remove castling rights for that side
            if p.color == WHITE:
                self.castling = self.castling.replace("K","").replace("Q","")
            else:
                self.castling = self.castling.replace("k","").replace("q","")
            capture = False
        else:
            # en passant capture
            if mv.en_passant:
                if old_ep is None or mv.to != old_ep:
                    raise ValueError("Invalid en passant target")
                # captured pawn is behind target
                dir_ = -1 if p.color == WHITE else 1
                cap_sq = (mv.to[0] - dir_, mv.to[1])
                cap_piece = self.piece_at(cap_sq)
                if not cap_piece or cap_piece.kind != "p" or cap_piece.color == p.color:
                    raise ValueError("Invalid en passant capture")
                self.set_piece(cap_sq, None)
                capture = True

            # move piece
            self.set_piece(mv.fr, None)

            moved_piece = p
            # promotion
            if p.kind == "p":
                promo_row = 0 if p.color == WHITE else 7
                if mv.to[0] == promo_row:
                    moved_piece = Piece(p.color, mv.promo or "q")
                # double move sets ep target (square passed over)
                start_row = 6 if p.color == WHITE else 1
                if mv.fr[0] == start_row and abs(mv.to[0] - mv.fr[0]) == 2:
                    passed = ((mv.fr[0] + mv.to[0]) // 2, mv.fr[1])
                    self.ep_target = passed

            self.set_piece(mv.to, moved_piece)

            # update castling rights if king or rook moved/captured
            if p.kind == "k":
                if p.color == WHITE:
                    self.castling = self.castling.replace("K","").replace("Q","")
                else:
                    self.castling = self.castling.replace("k","").replace("q","")
            if p.kind == "r":
                # rook moved from original squares
                if p.color == WHITE:
                    if mv.fr == (7,0): self.castling = self.castling.replace("Q","")
                    if mv.fr == (7,7): self.castling = self.castling.replace("K","")
                else:
                    if mv.fr == (0,0): self.castling = self.castling.replace("q","")
                    if mv.fr == (0,7): self.castling = self.castling.replace("k","")

            # rook captured on original squares
            if capture:
                t = self.piece_at(mv.to)  # now moved_piece; need infer captured from before, so do it via destination square info:
                # We can't retrieve captured rook post-move; instead use mv.to to strip rights if it was a rook start square.
                if mv.to == (7,0): self.castling = self.castling.replace("Q","")
                if mv.to == (7,7): self.castling = self.castling.replace("K","")
                if mv.to == (0,0): self.castling = self.castling.replace("q","")
                if mv.to == (0,7): self.castling = self.castling.replace("k","")

        # halfmove reset conditions
        if p.kind == "p" or capture:
            self.halfmove = 0
        else:
            self.halfmove += 1

        # flip turn / fullmove
        self.turn = WHITE if self.turn == BLACK else BLACK
        if self.turn == WHITE:
            self.fullmove += 1


# ---------------------------- Timeline Graph ---------------------------------

@dataclass
class Timeline:
    tid: int
    board: Board
    history: List[Board] = field(default_factory=list)  # snapshots (past)
    parent: Optional[int] = None
    children: Set[int] = field(default_factory=set)

    def snapshot(self) -> None:
        self.history.append(self.board.clone())
        if len(self.history) > 60:
            self.history = self.history[-60:]


@dataclass
class Game5D:
    X: int = 7
    future_branch_cap: int = 10   # limit children expansions per timeline per expand() call
    rng: random.Random = field(default_factory=random.Random)

    timelines: Dict[int, Timeline] = field(default_factory=dict)
    active_ids: Set[int] = field(default_factory=set)
    frozen_ids: Set[int] = field(default_factory=set)

    center_id: int = 0
    visible_ids: List[int] = field(default_factory=list)

    next_tid: int = 1
    sig_to_tid: Dict[str,int] = field(default_factory=dict)

    def __post_init__(self):
        root = Timeline(0, Board.starting())
        self.timelines[0] = root
        self.active_ids.add(0)
        self.sig_to_tid[root.board.signature()] = 0
        self.recenter("mix")

    # -------- graph expansion (future neighbors) --------

    def _get_or_create_timeline(self, b: Board, parent: Optional[int]) -> int:
        sig = b.signature()
        if sig in self.sig_to_tid:
            tid = self.sig_to_tid[sig]
            # link graph
            if parent is not None:
                self.timelines[parent].children.add(tid)
                if self.timelines[tid].parent is None:
                    self.timelines[tid].parent = parent
            return tid
        tid = self.next_tid
        self.next_tid += 1
        tl = Timeline(tid, b.clone(), history=[b.clone()], parent=parent)
        self.timelines[tid] = tl
        self.sig_to_tid[sig] = tid
        if parent is not None:
            self.timelines[parent].children.add(tid)
        self.active_ids.add(tid)
        return tid

    def expand(self) -> None:
        """
        Lazily generate future-adjacent realities:
        For each visible timeline, generate children by applying a sample of legal moves.
        """
        for tid in list(self.visible_ids):
            if tid in self.frozen_ids:
                continue
            tl = self.timelines[tid]
            moves = tl.board.legal_moves()
            if not moves:
                continue
            self.rng.shuffle(moves)
            for mv in moves[: self.future_branch_cap]:
                b2 = tl.board.clone()
                b2.push(mv)
                self._get_or_create_timeline(b2, parent=tid)

    # -------- adjacency --------

    def neighbors(self, tid: int) -> Set[int]:
        tl = self.timelines[tid]
        nbrs: Set[int] = set()
        if tl.parent is not None:
            nbrs.add(tl.parent)
        nbrs |= set(tl.children)
        return nbrs

    # -------- visibility sampling / re-centering --------

    def recenter(self, mode: str = "mix") -> None:
        """
        Re-center observable cone around center_id, then sample X visible timelines.
        mode biases selection: past|future|mix.
        """
        mode = mode.lower().strip()
        if mode not in ("past","future","mix"):
            raise ValueError("mode must be past|future|mix")

        # Ensure we have some future adjacency to look at
        self.expand()

        # Candidate set: center + neighbors + neighbors-of-neighbors (small cone)
        cand: Set[int] = set([self.center_id])
        for n in self.neighbors(self.center_id):
            cand.add(n)
            for nn in self.neighbors(n):
                cand.add(nn)

        # filter to active (not frozen)
        cand = {tid for tid in cand if tid in self.active_ids and tid not in self.frozen_ids}
        if not cand:
            self.visible_ids = []
            return

        # weight candidates by "pastness" and "futureness"
        weighted: List[Tuple[int, float]] = []
        for tid in cand:
            tl = self.timelines[tid]
            past_rich = len(tl.history)
            future_rich = len(tl.board.legal_moves())
            # also bias proximity to center
            dist = 0 if tid == self.center_id else (1 if tid in self.neighbors(self.center_id) else 2)

            if mode == "past":
                w = 1.0 + 0.9*past_rich + 0.1*future_rich
            elif mode == "future":
                w = 1.0 + 0.1*past_rich + 0.9*future_rich
            else:
                w = 1.0 + 0.35*past_rich + 0.35*future_rich

            w *= (1.6 if dist == 0 else (1.2 if dist == 1 else 1.0))
            weighted.append((tid, max(1.0, w)))

        # sample without replacement
        pool = weighted[:]
        self.rng.shuffle(pool)
        chosen: List[int] = []
        k = min(self.X, len(pool))
        for _ in range(k):
            total = sum(w for _, w in pool)
            pick = self.rng.random() * total
            cum = 0.0
            idx = 0
            for i, (_, w) in enumerate(pool):
                cum += w
                if cum >= pick:
                    idx = i
                    break
            chosen.append(pool[idx][0])
            pool.pop(idx)

        # ensure center is visible if possible
        if self.center_id in cand and self.center_id not in chosen:
            chosen[-1] = self.center_id

        self.visible_ids = chosen

    # -------- temporal re-centering constrained by piece existence --------

    def temporal_center(self, square_uci: str, direction: str) -> bool:
        """
        Attempt to shift center to an adjacent reality (parent/children) where
        the piece currently on `square_uci` (in the center timeline) also exists
        on that same square in the target timeline.

        Identity approximation: same color+kind on that square.
        Returns True if center changed.
        """
        sq = uci_to_sq(square_uci.lower().strip())
        direction = direction.lower().strip()
        center = self.timelines[self.center_id]
        piece = center.board.piece_at(sq)
        if not piece:
            return False

        # choose candidate neighbors based on direction
        nbrs = self.neighbors(self.center_id)
        if direction == "past":
            nbrs = {n for n in nbrs if self.timelines[self.center_id].parent == n}
        elif direction == "future":
            nbrs = {n for n in nbrs if n in self.timelines[self.center_id].children}
        elif direction == "mix":
            pass
        else:
            raise ValueError("direction must be past|future|mix")

        valid: List[int] = []
        for n in nbrs:
            if n in self.frozen_ids or n not in self.active_ids:
                continue
            p2 = self.timelines[n].board.piece_at(sq)
            if p2 and p2.color == piece.color and p2.kind == piece.kind:
                valid.append(n)

        if not valid:
            return False

        self.center_id = self.rng.choice(valid)
        self.recenter("mix")
        return True

    # -------- universal move --------

    def universal_move(self, mv_uci: str) -> Dict[str, List[int]]:
        """
        Apply one UCI move as a universal operation across visible timelines.
        Timelines where move is illegal become frozen (inaccessible).
        """
        mv_basic = parse_move_uci(mv_uci)

        applied: List[int] = []
        frozen: List[int] = []

        # Important: legality differs per timeline because of ep/castling rights etc.
        for tid in list(self.visible_ids):
            if tid in self.frozen_ids:
                continue
            tl = self.timelines[tid]
            # find the exact legal move object (to capture castle/en_passant flags)
            legal = {m.uci(): m for m in tl.board.legal_moves()}
            if mv_basic.uci() in legal:
                m = legal[mv_basic.uci()]
                tl.snapshot()
                tl.board.push(m)
                # the moved-to board may already exist as another timeline; link it as a child
                self._get_or_create_timeline(tl.board, parent=tid)
                applied.append(tid)
            else:
                self.frozen_ids.add(tid)
                self.active_ids.discard(tid)
                frozen.append(tid)

        if not self.active_ids:
            return {"applied": applied, "frozen": frozen}

        # after a move, drift awareness (mix), and expand a bit
        self.recenter("mix")
        return {"applied": applied, "frozen": frozen}

    # -------- observational check --------

    def observable_in_check(self, color: str) -> bool:
        if not self.visible_ids:
            return False
        for tid in self.visible_ids:
            tl = self.timelines[tid]
            if not tl.board.in_check(color):
                return False
        return True

    # -------- UI --------

    def show(self) -> None:
        print("\n=== VISIBLE TIMELINES (Observable Cone) ===")
        print(f"Center timeline: {self.center_id} | Visible count: {len(self.visible_ids)}")
        for i, tid in enumerate(self.visible_ids):
            tl = self.timelines[tid]
            tag = "CENTER" if tid == self.center_id else ""
            par = tl.parent if tl.parent is not None else "-"
            print(f"\n[#{i}] Timeline {tid} {tag} | turn={tl.board.turn} | hist={len(tl.history)} | parent={par} | children={len(tl.children)}")
            print(tl.board.ascii())

        print(f"\nActive timelines: {len(self.active_ids)} | Frozen: {len(self.frozen_ids)} | Total known: {len(self.timelines)}")
        for col in (WHITE, BLACK):
            if self.observable_in_check(col):
                print(f"OBSERVABLE CHECK: {col}")
        print()

# ---------------------------- CLI --------------------------------------------

def main():
    g = Game5D(X=7)
    print("Observable 5D Chess — all-of-the-above build. Type 'help'.\n")
    g.show()

    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye.")
            return

        if not line:
            continue

        cmd, *rest = line.split()

        if cmd in ("quit","exit"):
            print("bye.")
            return

        if cmd == "help":
            print(
                "Commands:\n"
                "  show\n"
                "  move e2e4        (UCI)\n"
                "  move e7e8q       (promotion)\n"
                "  recenter past|future|mix\n"
                "  tcenter e4 past  (temporal re-center constrained by piece existing on that square)\n"
                "  expand           (generate more adjacent futures)\n"
                "  quit\n"
            )
            continue

        if cmd == "show":
            g.show()
            continue

        if cmd == "expand":
            g.expand()
            g.recenter("mix")
            g.show()
            continue

        if cmd == "recenter":
            mode = rest[0] if rest else "mix"
            try:
                g.recenter(mode)
                g.show()
            except Exception as e:
                print("error:", e)
            continue

        if cmd == "tcenter":
            if len(rest) != 2:
                print("usage: tcenter <square> <past|future|mix>")
                continue
            sq, direction = rest
            try:
                ok = g.temporal_center(sq, direction)
                if not ok:
                    print("No adjacent reality matches (piece must already exist on that square).")
                g.show()
            except Exception as e:
                print("error:", e)
            continue

        if cmd == "move":
            if not rest:
                print("usage: move e2e4")
                continue
            try:
                out = g.universal_move(rest[0])
                print("applied to timelines:", out["applied"])
                print("frozen timelines:", out["frozen"])

                if not g.active_ids:
                    print("All timelines inaccessible. Reality has no playable branch left. Game over.")
                    return

                g.show()
            except Exception as e:
                print("error:", e)
            continue

        print("unknown command; type 'help'")

if __name__ == "__main__":
    main()
