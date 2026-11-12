import hashlib
import hmac
from dataclasses import dataclass, replace
from enum import Enum

from nepal_retail.payments.repository import PaymentRecord, PaymentRepository
from nepal_retail.payments.state import PaymentIntent, PaymentState
from nepal_retail.voice.contracts import text

FORBIDDEN_CARD_FIELDS = frozenset(
	{
		"pan",
		"card_number",
		"pin",
		"pin_block",
		"cvv",
		"cvc",
		"cvv2",
		"track1",
		"track2",
		"track_data",
		"magnetic_stripe",
		"emv_tlv_raw",
	}
)


def _hash_secret(shared_secret: str) -> str:
	cleaned = text(shared_secret, "device shared secret", 200)
	if len(cleaned) < 16:
		raise ValueError("Device shared secret must be at least 16 characters")
	return hashlib.sha256(cleaned.encode()).hexdigest()


class CommandState(str, Enum):
	QUEUED = "queued"
	DISPATCHED = "dispatched"
	COMPLETED = "completed"
	EXPIRED = "expired"


@dataclass(frozen=True)
class TerminalDevice:
	device_id: str
	company: str
	pos_profile: str
	secret_hash: str
	revoked: bool = False


@dataclass(frozen=True)
class TerminalCommand:
	command_id: str
	device_id: str
	intent_id: str
	company: str
	order_revision: str
	amount_minor: int
	currency: str
	created_at: int
	expires_at: int
	state: CommandState = CommandState.QUEUED


def assert_no_cardholder_data(metadata: dict | None) -> None:
	if metadata is None:
		return
	if not isinstance(metadata, dict):
		raise ValueError("Terminal metadata must be a dictionary")
	for key, value in metadata.items():
		normalized = str(key).strip().lower()
		if normalized in FORBIDDEN_CARD_FIELDS:
			raise ValueError(f"Forbidden cardholder data field rejected: {key}")
		if isinstance(value, dict):
			assert_no_cardholder_data(value)


class TerminalAgentCoordinator:
	def __init__(self):
		self._devices: dict[str, TerminalDevice] = {}
		self._commands: dict[str, TerminalCommand] = {}

	def register_device(
		self,
		device_id: str,
		company: str,
		pos_profile: str,
		shared_secret: str,
	) -> TerminalDevice:
		cleaned_id = text(device_id, "device ID", 140)
		if cleaned_id in self._devices:
			raise ValueError(f"Terminal device {cleaned_id} is already registered")
		device = TerminalDevice(
			device_id=cleaned_id,
			company=text(company, "company", 140),
			pos_profile=text(pos_profile, "POS Profile", 140),
			secret_hash=_hash_secret(shared_secret),
		)
		self._devices[cleaned_id] = device
		return device

	def revoke_device(self, device_id: str) -> TerminalDevice:
		cleaned_id = text(device_id, "device ID", 140)
		if cleaned_id not in self._devices:
			raise KeyError(f"Unknown terminal device: {cleaned_id}")
		revoked = replace(self._devices[cleaned_id], revoked=True)
		self._devices[cleaned_id] = revoked
		return revoked

	def _authenticate_device(self, device_id: str, shared_secret: str) -> TerminalDevice:
		cleaned_id = text(device_id, "device ID", 140)
		device = self._devices.get(cleaned_id)
		if device is None:
			raise PermissionError("Unregistered terminal device")
		supplied_hash = _hash_secret(shared_secret)
		if not hmac.compare_digest(device.secret_hash, supplied_hash):
			raise PermissionError("Invalid terminal device credentials")
		if device.revoked:
			raise PermissionError("Terminal device has been revoked")
		return device

	def enqueue_command(
		self,
		command_id: str,
		device_id: str,
		intent: PaymentIntent,
		*,
		now: int,
		ttl_seconds: int = 120,
	) -> TerminalCommand:
		cleaned_cmd = text(command_id, "command ID", 140)
		if cleaned_cmd in self._commands:
			raise ValueError(f"Terminal command {cleaned_cmd} already exists")
		cleaned_device = text(device_id, "device ID", 140)
		device = self._devices.get(cleaned_device)
		if device is None or device.revoked:
			raise PermissionError("Terminal device is not active")
		if not isinstance(intent, PaymentIntent) or intent.state != PaymentState.PROCESSING:
			raise ValueError("Terminal command requires a processing PaymentIntent")
		if device.company != intent.company:
			raise PermissionError("Terminal device company does not match payment intent")
		if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int) or not 30 <= ttl_seconds <= 300:
			raise ValueError("Terminal command TTL must be between 30 and 300 seconds")

		command = TerminalCommand(
			command_id=cleaned_cmd,
			device_id=device.device_id,
			intent_id=intent.intent_id,
			company=intent.company,
			order_revision=intent.order_revision,
			amount_minor=intent.amount_minor,
			currency=intent.currency,
			created_at=now,
			expires_at=now + ttl_seconds,
		)
		self._commands[cleaned_cmd] = command
		return command

	def poll_next_command(
		self,
		device_id: str,
		shared_secret: str,
		*,
		now: int,
	) -> TerminalCommand | None:
		device = self._authenticate_device(device_id, shared_secret)
		for cmd_id, command in list(self._commands.items()):
			if command.device_id != device.device_id:
				continue
			if command.state in (CommandState.COMPLETED, CommandState.EXPIRED):
				continue
			if now > command.expires_at:
				self._commands[cmd_id] = replace(command, state=CommandState.EXPIRED)
				continue
			dispatched = replace(command, state=CommandState.DISPATCHED)
			self._commands[cmd_id] = dispatched
			return dispatched
		return None

	def report_terminal_result(
		self,
		repository: PaymentRepository,
		*,
		device_id: str,
		shared_secret: str,
		command_id: str,
		event_id: str,
		status: str,
		amount_minor: int,
		currency: str,
		provider_reference: str | None = None,
		metadata: dict | None = None,
	) -> PaymentRecord:
		device = self._authenticate_device(device_id, shared_secret)
		assert_no_cardholder_data(metadata)
		cleaned_cmd = text(command_id, "command ID", 140)
		command = self._commands.get(cleaned_cmd)
		if command is None or command.device_id != device.device_id:
			raise ValueError("Unknown terminal command for this device")
		if command.state == CommandState.EXPIRED:
			raise ValueError("Expired terminal command result requires manual reconciliation")

		if status == "approved":
			record = repository.apply_verified_event(
				provider=f"terminal:{device.device_id}",
				event_id=event_id,
				intent_id=command.intent_id,
				amount_minor=amount_minor,
				currency=currency,
				provider_reference=provider_reference or "",
			)
			self._commands[cleaned_cmd] = replace(command, state=CommandState.COMPLETED)
			return record
		if status in ("unknown", "timeout", "declined"):
			record = repository.get(command.intent_id)
			if record.intent.state == PaymentState.PROCESSING:
				record = repository.mark_unknown(command.intent_id)
			self._commands[cleaned_cmd] = replace(command, state=CommandState.COMPLETED)
			return record
		raise ValueError(f"Unsupported terminal result status: {status}")
