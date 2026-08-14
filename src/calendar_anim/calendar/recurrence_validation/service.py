from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from calendar_anim.calendar.models import CalendarDeleteResult, CalendarInfo
from calendar_anim.calendar.recurrence_validation.artifacts import RecurrenceValidationStore
from calendar_anim.calendar.recurrence_validation.gateway import (
    ValidationInsertError,
    ValidationRemoteResource,
)
from calendar_anim.calendar.recurrence_validation.models import (
    RecurrenceValidationPlan,
    ValidationCleanupResult,
    ValidationResourcePlan,
    ValidationStatus,
    ValidationUploadState,
)
from calendar_anim.exceptions import CalendarAnimError


class RecurrenceValidationGateway(Protocol):
    def list_window_resources(
        self, calendar_id: str, start: datetime, end: datetime
    ) -> list[ValidationRemoteResource]: ...

    def find_validation_resources(
        self, calendar_id: str, metadata: Mapping[str, str]
    ) -> list[ValidationRemoteResource]: ...

    def insert_validation_resource(
        self, calendar_id: str, resource: ValidationResourcePlan
    ) -> str: ...

    def delete_events(self, calendar_id: str, event_ids: Sequence[str]) -> CalendarDeleteResult: ...


CalendarResolver = Callable[[str, str], tuple[CalendarInfo, bool]]


class ValidationPlan(Protocol):
    validation_id: str
    calendar_profile: str
    calendar_name: str
    timezone: str
    resources: list[ValidationResourcePlan]

    @property
    def first_week(self) -> date: ...

    @property
    def last_week(self) -> date: ...


class ValidationArtifactStore(Protocol):
    def load_state(self, validation_id: str) -> ValidationUploadState | None: ...

    def save_state(self, state: ValidationUploadState) -> object: ...

    def save_cleanup(self, result: ValidationCleanupResult) -> object: ...


class RecurrenceValidationService:
    def __init__(
        self,
        gateway: RecurrenceValidationGateway,
        store: RecurrenceValidationStore | ValidationArtifactStore,
        resolve_calendar: CalendarResolver,
    ) -> None:
        self.gateway = gateway
        self.store = store
        self.resolve_calendar = resolve_calendar

    @staticmethod
    def metadata(validation_id: str, calendar_profile: str = "account-a") -> dict[str, str]:
        return {
            "generated_by": "calendar-anim-recurrence-validation",
            "validation_id": validation_id,
            "calendar_profile": calendar_profile,
        }

    def upload(self, plan: RecurrenceValidationPlan | ValidationPlan) -> ValidationUploadState:
        calendar, created = self.resolve_calendar(plan.calendar_name, plan.timezone)
        if created:
            raise CalendarAnimError(
                "Refusing smallest-real-validation because its lab calendar did not already exist"
            )
        expected_metadata = self.metadata(plan.validation_id, plan.calendar_profile)
        zone = ZoneInfo(plan.timezone)
        window_start = datetime.combine(plan.first_week, datetime.min.time(), zone)
        window_end = datetime.combine(plan.last_week + timedelta(days=7), datetime.min.time(), zone)
        window_events = self.gateway.list_window_resources(calendar.id, window_start, window_end)
        unrelated = [
            resource
            for resource in window_events
            if any(resource.metadata.get(key) != value for key, value in expected_metadata.items())
        ]
        if unrelated:
            raise CalendarAnimError(
                f"Preflight found {len(unrelated)} unrelated event(s) in the six validation "
                "weeks; no insert was attempted"
            )
        remote = self.gateway.find_validation_resources(calendar.id, expected_metadata)
        remote_ids = {resource.event_id for resource in remote}
        state = self.store.load_state(plan.validation_id) or ValidationUploadState(
            validation_id=plan.validation_id,
            calendar_profile=plan.calendar_profile,
            calendar_id=calendar.id,
            updated_at=datetime.now(UTC),
        )
        if state.calendar_profile != plan.calendar_profile:
            raise CalendarAnimError("Validation checkpoint belongs to a different Calendar profile")
        if state.calendar_id is not None and state.calendar_id != calendar.id:
            raise CalendarAnimError("Validation checkpoint belongs to a different Calendar ID")
        state.calendar_id = calendar.id
        state.reconciled_resource_ids = sorted(remote_ids)
        state.created_resource_ids = sorted(set(state.created_resource_ids) | remote_ids)
        self.store.save_state(state)
        for resource in plan.resources:
            if resource.event_id in remote_ids:
                continue
            state.events_insert_calls += 1
            try:
                created_id = self.gateway.insert_validation_resource(calendar.id, resource)
            except ValidationInsertError as error:
                state.status = ValidationStatus.PARTIAL
                state.rate_limit_exceeded_count += int(error.rate_limited)
                state.quota_exceeded_count += int(error.quota_exceeded)
                state.errors.append(str(error))
                self.store.save_state(state)
                raise CalendarAnimError(
                    "Validation upload stopped safely after Calendar rejected an insert; "
                    "existing validation resources were preserved"
                ) from error
            state.created_resource_ids = sorted(
                set(state.created_resource_ids) | {created_id, resource.event_id}
            )
            self.store.save_state(state)
        state.status = ValidationStatus.COMPLETED
        self.store.save_state(state)
        return state

    def cleanup(
        self,
        plan: RecurrenceValidationPlan | ValidationPlan,
        calendar: CalendarInfo,
    ) -> ValidationCleanupResult:
        metadata = self.metadata(plan.validation_id, plan.calendar_profile)
        resources = self.gateway.find_validation_resources(calendar.id, metadata)
        resource_ids = sorted({resource.event_id for resource in resources})
        deletion = self.gateway.delete_events(calendar.id, resource_ids)
        result = ValidationCleanupResult(
            validation_id=plan.validation_id,
            matched_resource_ids=resource_ids,
            deleted_resources=deletion.deleted_events,
            failed_deletions=deletion.failed_events,
            errors=deletion.errors,
        )
        self.store.save_cleanup(result)
        return result
