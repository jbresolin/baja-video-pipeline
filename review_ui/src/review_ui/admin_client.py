"""The admin pages' calls to the orchestrator (/admin/*), as the signed-in admin.

Same rules as OrchestratorClient: the browser's session goes along as a
bearer token, and the orchestrator decides (403 for a reviewer).
"""

from urllib.parse import quote

from review_ui.orchestrator_client import OrchestratorClient
from shared.models import (
    AdminArea,
    Contact,
    ContactsResponse,
    ContactStatus,
    CreateContactRequest,
    CreateEnrollmentTokenRequest,
    CreateProvaRequest,
    CreateUserRequest,
    EnrollmentTokenCreated,
    EnrollmentTokensResponse,
    Event,
    EventsResponse,
    Prova,
    ProvasResponse,
    ReorderProvasRequest,
    ResultsSyncReport,
    SaveTeamRequest,
    SettingsResponse,
    TeamRow,
    TeamsResponse,
    UpdateContactRequest,
    UpdateProvaRequest,
    UpdateSettingsRequest,
    UpdateUserRequest,
    UpdateWorkerRequest,
    User,
    UsersResponse,
    Worker,
)


def _segment(value: str | int) -> str:
    return quote(str(value), safe="")


def _body(model) -> dict:
    # Only what the form actually set: a field left out keeps its value,
    # an explicit None (e.g. "no default prova") clears it.
    return model.model_dump(mode="json", exclude_unset=True)


class AdminClient(OrchestratorClient):
    # --- Users ---

    def users(self) -> list[User]:
        return self._parse(UsersResponse, self._request("GET", "/admin/users")).users

    def create_user(self, request: CreateUserRequest) -> User:
        return self._parse(User, self._request("POST", "/admin/users", json=_body(request)))

    def update_user(self, username: str, request: UpdateUserRequest) -> User:
        path = f"/admin/users/{_segment(username)}"
        return self._parse(User, self._request("PATCH", path, json=_body(request)))

    # --- Contacts ---

    def contacts(self, status: ContactStatus | None = None) -> list[Contact]:
        params = {"status": status.value} if status else {}
        response = self._request("GET", "/admin/contacts", params=params)
        return self._parse(ContactsResponse, response).contacts

    def create_contact(self, request: CreateContactRequest) -> Contact:
        return self._parse(Contact, self._request("POST", "/admin/contacts", json=_body(request)))

    def update_contact(self, phone: str, request: UpdateContactRequest) -> Contact:
        path = f"/admin/contacts/{_segment(phone)}"
        return self._parse(Contact, self._request("PATCH", path, json=_body(request)))

    # --- Provas ---

    def create_prova(self, name: str) -> Prova:
        body = _body(CreateProvaRequest(name=name))
        return self._parse(Prova, self._request("POST", "/admin/provas", json=body))

    def update_prova(self, prova_id: int, request: UpdateProvaRequest) -> Prova:
        path = f"/admin/provas/{_segment(prova_id)}"
        return self._parse(Prova, self._request("PATCH", path, json=_body(request)))

    def reorder_provas(self, prova_ids: list[int]) -> list[Prova]:
        body = _body(ReorderProvasRequest(prova_ids=prova_ids))
        return self._parse(
            ProvasResponse, self._request("PUT", "/admin/provas/order", json=body)
        ).provas

    # --- Teams ---

    def teams(self) -> list[TeamRow]:
        return self._parse(TeamsResponse, self._request("GET", "/admin/teams")).teams

    def save_team(self, car_number: int, request: SaveTeamRequest) -> TeamRow:
        path = f"/admin/teams/{_segment(car_number)}"
        return self._parse(TeamRow, self._request("PUT", path, json=_body(request)))

    def delete_team(self, car_number: int) -> None:
        self._request("DELETE", f"/admin/teams/{_segment(car_number)}")

    def sync_from_results_api(self) -> ResultsSyncReport:
        """Fetch the configured event's provas and teams now (the Sync button)."""
        return self._parse(ResultsSyncReport, self._request("POST", "/admin/results-sync"))

    # --- Enrollment and workers ---

    def enrollment_tokens(self) -> EnrollmentTokensResponse:
        response = self._request("GET", "/admin/enrollment-tokens")
        return self._parse(EnrollmentTokensResponse, response)

    def create_enrollment_token(
        self, request: CreateEnrollmentTokenRequest
    ) -> EnrollmentTokenCreated:
        # model_dump without exclude_unset: role and validity have defaults
        # the form always shows, so send them explicitly.
        body = request.model_dump(mode="json")
        response = self._request("POST", "/admin/enrollment-tokens", json=body)
        return self._parse(EnrollmentTokenCreated, response)

    def revoke_enrollment_token(self, token_id: str) -> None:
        self._request("POST", f"/admin/enrollment-tokens/{_segment(token_id)}/revoke")

    def rename_worker(self, worker_id: str, name: str) -> Worker:
        body = _body(UpdateWorkerRequest(name=name))
        path = f"/admin/workers/{_segment(worker_id)}"
        return self._parse(Worker, self._request("PATCH", path, json=body))

    def revoke_worker(self, worker_id: str) -> Worker:
        response = self._request("POST", f"/admin/workers/{_segment(worker_id)}/revoke")
        return self._parse(Worker, response)

    # --- Settings and audit ---

    def settings(self) -> SettingsResponse:
        return self._parse(SettingsResponse, self._request("GET", "/admin/settings"))

    def update_settings(self, request: UpdateSettingsRequest) -> SettingsResponse:
        response = self._request("PATCH", "/admin/settings", json=_body(request))
        return self._parse(SettingsResponse, response)

    def audit(self, area: AdminArea, limit: int = 10) -> list[Event]:
        params = {"area": area.value, "limit": limit}
        return self._parse(
            EventsResponse, self._request("GET", "/admin/audit", params=params)
        ).events
