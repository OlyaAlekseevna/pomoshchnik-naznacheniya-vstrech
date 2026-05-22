from enum import StrEnum


class RequestStatus(StrEnum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    UPDATED_BY_USER = "updated_by_user"
    CANCELED_BY_USER = "canceled_by_user"
    APPROVED = "approved"
    REJECTED = "rejected"
    SLOT_UNAVAILABLE = "slot_unavailable"
    RESERVATION_EXPIRED = "reservation_expired"
    EVENT_CREATION_ERROR = "event_creation_error"


class RequestChangedByRole(StrEnum):
    USER = "user"
    ADMIN = "admin"
    SYSTEM = "system"


class ReservationStatus(StrEnum):
    ACTIVE = "active"
    RELEASED = "released"
    EXPIRED = "expired"


class GoogleEventStatus(StrEnum):
    PENDING = "pending"
    CREATED = "created"
    FAILED = "failed"
