import hashlib
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from spaceten.errors import InvariantError, StaleParent, WorldExists
from spaceten.kernel.energy import (
    AccountView,
    Energy,
    EnergyExhausted,
    _Account,
    io_cost_mj,
)
from spaceten.kernel.event import Act, Event, Failed, Finish, Init, Observe, Op, Plan
from spaceten.kernel.invariant import invariant_issues, spend_from
from spaceten.kernel.number import Id, IdFactory, new_id
from spaceten.kernel.space import (
    Address,
    Cell,
    CellNotFound,
    OutsideSpace,
    Workspace,
    WriteTooLarge,
)
from spaceten.kernel.time import Clock
from spaceten.store.protocol import Store, WorldHeader

_DEFAULT_ENERGY_CAP = 100_000
_DEFAULT_MAX_WRITE_BYTES = 1_048_576
_RESERVED_DIR = ".spaceten"


@dataclass(frozen=True)
class Receipt:
    event: Event
    remaining: Energy
    cell: Cell | None = None
    bytes_read: bytes | None = None


@dataclass(frozen=True)
class CheckIssue:
    code: str
    message: str


@dataclass(frozen=True)
class CheckReport:
    ok: bool
    issues: tuple[CheckIssue, ...]
    events: int
    remaining: Energy


def _require_store(store: Store | None) -> Store:
    if store is None:
        raise NotImplementedError(
            "JsonlStore is not implemented; pass store=MemoryStore()"
        )
    return store


def _max_write_bytes(override: int | None) -> int:
    if override is not None:
        return override
    raw = os.environ.get("SPACETEN_MAX_WRITE_BYTES")
    if raw is None:
        return _DEFAULT_MAX_WRITE_BYTES
    return int(raw)


def _bind_store_root(store: Store, root: Path) -> None:
    if getattr(store, "root", None) is None and hasattr(store, "root"):
        store.root = root  # type: ignore[attr-defined]


def _replay_account(events: Sequence[Event], cap: Energy) -> _Account:
    account = _Account(cap=cap, spent=Energy(0), remaining=Energy(cap.mj))
    for event in events:
        spend = spend_from(event)
        if spend is not None:
            account.debit(spend.amount)
    return account


class World:
    def __init__(
        self,
        root: Path,
        store: Store,
        *,
        clock: Clock,
        ids: IdFactory,
        workspace: Workspace,
        header: WorldHeader,
        account: _Account,
        cells: dict[str, Cell],
        events: list[Event],
    ) -> None:
        self._root = root
        self._store = store
        self._clock = clock
        self._ids = ids
        self._workspace = workspace
        self._header = header
        self._account = account
        self._cells = cells
        self._events = events

    @classmethod
    def init(
        cls,
        root: Path,
        *,
        energy_cap: int = _DEFAULT_ENERGY_CAP,
        store: Store | None = None,
        clock: Clock | None = None,
        ids: IdFactory | None = None,
        max_write_bytes: int | None = None,
    ) -> "World":
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        if (root / _RESERVED_DIR).exists():
            raise WorldExists(str(root / _RESERVED_DIR))
        store = _require_store(store)
        resolved = root.resolve()
        _bind_store_root(store, resolved)
        clock = clock or Clock()
        ids = ids or new_id
        created = clock.now()
        header = WorldHeader(
            id=ids(),
            created_at=created,
            energy_cap=energy_cap,
        )
        account = _Account(
            cap=Energy(energy_cap),
            spent=Energy(0),
            remaining=Energy(energy_cap),
        )
        world = cls(
            resolved,
            store,
            clock=clock,
            ids=ids,
            workspace=Workspace(
                resolved, max_write_bytes=_max_write_bytes(max_write_bytes)
            ),
            header=header,
            account=account,
            cells={},
            events=[],
        )
        with store.lock():
            store.save_header(header)
            event = Event(
                id=ids(),
                seq=1,
                wall=created,
                parent=None,
                actor="kernel",
                energy_delta_mj=0,
                energy_reason="none",
                op=Init(energy_cap_mj=energy_cap),
            )
            store.append(event)
            world._events.append(event)
            store.save_caches(account.view(), {})
            world._assert_invariants()
        return world

    @classmethod
    def load(
        cls,
        root: Path,
        *,
        store: Store | None = None,
        clock: Clock | None = None,
        ids: IdFactory | None = None,
        max_write_bytes: int | None = None,
    ) -> "World":
        store = _require_store(store)
        resolved = Path(root).resolve()
        _bind_store_root(store, resolved)
        with store.lock():
            events = store.load_events()
            store.recover_writes(events)
            events = store.load_events()
            if not events:
                raise FileNotFoundError("world has no events")
            header = store.load_header()
            caches = store.load_caches()
            account = _replay_account(events, Energy(header.energy_cap))
            cells = dict(caches[1]) if caches is not None else {}
            world = cls(
                resolved,
                store,
                clock=clock or Clock(),
                ids=ids or new_id,
                workspace=Workspace(
                    resolved, max_write_bytes=_max_write_bytes(max_write_bytes)
                ),
                header=header,
                account=account,
                cells=cells,
                events=list(events),
            )
            world._assert_invariants()
        return world

    @property
    def account(self) -> AccountView:
        return self._account.view()

    @property
    def header(self) -> WorldHeader:
        return self._header

    @property
    def head(self) -> Event | None:
        return self._events[-1] if self._events else None

    def propose(
        self,
        actor: str,
        op: Op,
        *,
        spend: Energy | None = None,
        parent: Id | None = None,
        data: bytes | None = None,
    ) -> Receipt:
        with self._store.lock():
            return self._propose(actor, op, spend=spend, parent=parent, data=data)

    def check(self, *, rebuild: bool = False) -> CheckReport:
        if rebuild:
            with self._store.lock():
                return self._check(rebuild=True)
        return self._check(rebuild=False)

    def replay(self) -> None:
        self._account = _replay_account(self._events, Energy(self._header.energy_cap))

    def _propose(
        self,
        actor: str,
        op: Op,
        *,
        spend: Energy | None,
        parent: Id | None,
        data: bytes | None,
    ) -> Receipt:
        committed_parent = self._committed_parent(parent)
        if isinstance(op, Init):
            raise ValueError("Init is created by World.init")
        if isinstance(op, Finish):
            return self._propose_finish(actor, op, spend, committed_parent)
        if isinstance(op, Plan):
            return self._propose_plan(actor, op, spend, committed_parent)
        if isinstance(op, Failed):
            return self._propose_failed(actor, op, spend, committed_parent)
        if isinstance(op, Observe):
            return self._propose_observe(actor, op, spend, committed_parent)
        if isinstance(op, Act):
            return self._propose_act(actor, op, spend, committed_parent, data)
        raise TypeError(f"unsupported op: {type(op).__name__}")

    def _propose_finish(
        self,
        actor: str,
        op: Finish,
        spend: Energy | None,
        parent: str | None,
    ) -> Receipt:
        if spend is not None:
            raise ValueError("Finish spend must be None")
        return self._commit(
            actor,
            op,
            parent=parent,
            energy_delta_mj=0,
            energy_reason="none",
        )

    def _propose_plan(
        self,
        actor: str,
        op: Plan,
        spend: Energy | None,
        parent: str | None,
    ) -> Receipt:
        if spend is None or spend.mj <= 0:
            raise ValueError("Plan spend must be a positive Energy")
        self._require_energy(spend.mj)
        return self._commit(
            actor,
            op,
            parent=parent,
            energy_delta_mj=-spend.mj,
            energy_reason="plan",
            debit=spend,
        )

    def _propose_failed(
        self,
        actor: str,
        op: Failed,
        spend: Energy | None,
        parent: str | None,
    ) -> Receipt:
        if spend is None:
            return self._commit(
                actor,
                op,
                parent=parent,
                energy_delta_mj=0,
                energy_reason="none",
            )
        if spend.mj <= 0:
            raise ValueError("Failed spend must be None or positive")
        remaining = self._account.view().remaining.mj
        debit_mj = min(spend.mj, remaining)
        if spend.mj > remaining:
            op = op.model_copy(update={"attempted_mj": spend.mj})
        if debit_mj == 0:
            return self._commit(
                actor,
                op,
                parent=parent,
                energy_delta_mj=0,
                energy_reason="none",
            )
        return self._commit(
            actor,
            op,
            parent=parent,
            energy_delta_mj=-debit_mj,
            energy_reason="plan",
            debit=Energy(debit_mj),
        )

    def _propose_observe(
        self,
        actor: str,
        op: Observe,
        spend: Energy | None,
        parent: str | None,
    ) -> Receipt:
        if spend is not None:
            raise ValueError("Observe spend must be None")
        addr = Address(op.address)
        if op.listing:
            quote = io_cost_mj("list_dir", 0)
            self._require_energy(quote)
            self._workspace.list_dir(addr)
            cell = self._workspace.observe(addr)
            committed = op.model_copy(
                update={"size_bytes": 0, "content_hash": None, "listing": True}
            )
            return self._commit(
                actor,
                committed,
                parent=parent,
                energy_delta_mj=-quote,
                energy_reason="io",
                debit=Energy(quote),
                cell=cell,
            )
        cell = self._workspace.observe(addr)
        quote = io_cost_mj("observe", cell.size_bytes)
        self._require_energy(quote)
        bytes_read = self._workspace.read(addr) if cell.kind == "file" else None
        committed = op.model_copy(
            update={"size_bytes": cell.size_bytes, "content_hash": cell.content_hash}
        )
        return self._commit(
            actor,
            committed,
            parent=parent,
            energy_delta_mj=-quote,
            energy_reason="io",
            debit=Energy(quote),
            cell=cell,
            bytes_read=bytes_read,
        )

    def _propose_act(
        self,
        actor: str,
        op: Act,
        spend: Energy | None,
        parent: str | None,
        data: bytes | None,
    ) -> Receipt:
        if spend is not None:
            raise ValueError("Act spend must be None")
        if data is None:
            raise ValueError("Act requires data")
        payload = bytes(data)
        addr = Address(op.address)
        dest = self._workspace.resolve(addr)
        if dest.exists() and dest.is_dir():
            raise IsADirectoryError(op.address)
        if len(payload) > self._workspace.max_write_bytes:
            raise WriteTooLarge(f"{len(payload)} > {self._workspace.max_write_bytes}")
        quote = io_cost_mj("write", len(payload))
        self._require_energy(quote)
        digest = hashlib.sha256(payload).hexdigest()
        if op.digest != digest or op.size_bytes != len(payload):
            raise ValueError("Act digest/size_bytes must match data")
        dest.parent.mkdir(parents=True, exist_ok=True)
        event_id = self._ids()
        staged = self._store.stage_write(event_id, dest, payload)
        event = self._new_event(
            actor,
            op,
            parent=parent,
            energy_delta_mj=-quote,
            energy_reason="io",
            event_id=event_id,
        )
        self._store.append(event)
        self._events.append(event)
        self._store.commit_write(staged, dest)
        self._account.debit(Energy(quote))
        cell = self._workspace.observe(addr)
        self._cells[addr.path] = cell
        self._store.save_caches(self._account.view(), self._cells)
        self._assert_invariants()
        return Receipt(
            event=event,
            remaining=self._account.view().remaining,
            cell=cell,
        )

    def _commit(
        self,
        actor: str,
        op: Op,
        *,
        parent: str | None,
        energy_delta_mj: int,
        energy_reason: Literal["io", "plan", "none"],
        debit: Energy | None = None,
        cell: Cell | None = None,
        bytes_read: bytes | None = None,
        event_id: Id | None = None,
    ) -> Receipt:
        event = self._new_event(
            actor,
            op,
            parent=parent,
            energy_delta_mj=energy_delta_mj,
            energy_reason=energy_reason,
            event_id=event_id,
        )
        self._store.append(event)
        self._events.append(event)
        if debit is not None and debit.mj:
            self._account.debit(debit)
        if cell is not None:
            self._cells[cell.address.path] = cell
        self._store.save_caches(self._account.view(), self._cells)
        self._assert_invariants()
        return Receipt(
            event=event,
            remaining=self._account.view().remaining,
            cell=cell,
            bytes_read=bytes_read,
        )

    def _new_event(
        self,
        actor: str,
        op: Op,
        *,
        parent: str | None,
        energy_delta_mj: int,
        energy_reason: Literal["io", "plan", "none"],
        event_id: Id | None = None,
    ) -> Event:
        return Event(
            id=event_id or self._ids(),
            seq=len(self._events) + 1,
            wall=self._clock.now(),
            parent=parent,
            actor=actor,
            energy_delta_mj=energy_delta_mj,
            energy_reason=energy_reason,
            op=op,
        )

    def _committed_parent(self, parent: Id | None) -> str | None:
        head = self.head
        expected = None if head is None else head.id
        if parent is None:
            return expected
        if parent != expected:
            raise StaleParent(parent)
        return parent

    def _require_energy(self, amount_mj: int) -> None:
        remaining = self._account.view().remaining
        requested = Energy(amount_mj)
        if requested.mj > remaining.mj:
            raise EnergyExhausted(remaining, requested)

    def _assert_invariants(self) -> None:
        issues = invariant_issues(
            self._events,
            self._account.view(),
            in_jail=self._address_in_jail,
        )
        if issues:
            code, message = issues[0]
            raise InvariantError(f"{code}: {message}")

    def _address_in_jail(self, address: str) -> bool:
        try:
            self._workspace.resolve(Address(address))
        except OutsideSpace:
            return False
        return True

    def _check(self, *, rebuild: bool) -> CheckReport:
        issues: list[CheckIssue] = []
        events = self._events
        account = self._account.view()
        if rebuild:
            rebuilt = _replay_account(events, Energy(self._header.energy_cap))
            caches = self._store.load_caches()
            if caches is not None and caches[0] != rebuilt.view():
                issues.append(
                    CheckIssue(
                        "cache_drift",
                        "energy cache does not match spend_from replay",
                    )
                )
            if account != rebuilt.view():
                issues.append(
                    CheckIssue(
                        "cache_drift",
                        "in-memory account does not match spend_from replay",
                    )
                )
            self._account = rebuilt
            account = rebuilt.view()
            issues.extend(self._dirty_space_issues())
        if events and isinstance(events[0].op, Init):
            if events[0].op.energy_cap_mj != self._header.energy_cap:
                issues.append(
                    CheckIssue(
                        "cache_drift",
                        "header energy_cap does not match Init.energy_cap_mj",
                    )
                )
        for code, message in invariant_issues(
            events, account, in_jail=self._address_in_jail
        ):
            issues.append(CheckIssue(code, message))
        return CheckReport(
            ok=not issues,
            issues=tuple(issues),
            events=len(events),
            remaining=account.remaining,
        )

    def _dirty_space_issues(self) -> list[CheckIssue]:
        last: dict[str, str] = {}
        for event in self._events:
            op = event.op
            if isinstance(op, Act):
                last[op.address] = op.digest
            elif (
                isinstance(op, Observe)
                and not op.listing
                and op.content_hash is not None
            ):
                last[op.address] = op.content_hash
        issues: list[CheckIssue] = []
        for address, digest in last.items():
            try:
                payload = self._workspace.read(Address(address))
            except (CellNotFound, OutsideSpace, IsADirectoryError, OSError):
                issues.append(
                    CheckIssue("dirty_space", f"{address} missing or unreadable")
                )
                continue
            if hashlib.sha256(payload).hexdigest() != digest:
                issues.append(CheckIssue("dirty_space", f"{address} hash mismatch"))
        return issues
