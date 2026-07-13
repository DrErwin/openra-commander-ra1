#region Copyright & License Information
/*
 * Copyright (c) The OpenRA Developers and Contributors
 * This file is part of OpenRA, which is free software. It is made
 * available under the terms of the GNU General Public License as published
 * by the Free Software Foundation, either version 3 of the License, or (at
 * your option) any later version. For more information, see COPYING.
 */
#endregion

using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Text.Json.Serialization;
using OpenRA.Mods.Common.Traits.BotModules;
using OpenRA.Traits;
using OpenRA.Support;

namespace OpenRA.Mods.Common.Traits.Commander
{
	public enum MissionDecisionCode
	{
		Accepted,
		Idempotent,
		InvalidSchema,
		SessionMismatch,
		RevisionGap,
		MissionNotFound,
		MissionConflict,
		MissionTypeNotEnabled,
		TargetUnknown,
		NoUnits,
		NoPrereq,
		InternalError,
	}

	public sealed class MissionDecision
	{
		public bool Accepted { get; init; }
		public MissionDecisionCode Code { get; init; }
		public string Message { get; init; }
	}

	public sealed class MissionDefinition
	{
		[JsonPropertyName("id")] public string Id { get; set; }
		[JsonPropertyName("type")] public string Type { get; set; }
		[JsonPropertyName("priority")] public string Priority { get; set; }
		[JsonPropertyName("ttl_ticks")] public int TtlTicks { get; set; }
		[JsonPropertyName("notes")] public string Notes { get; set; }
		[JsonPropertyName("target")] public string Target { get; set; }
		[JsonPropertyName("force_units")] public int ForceUnits { get; set; }
		[JsonPropertyName("unit_type")] public string UnitType { get; set; }
		[JsonPropertyName("escort_units")] public int EscortUnits { get; set; }
		[JsonPropertyName("actor_type")] public string ActorType { get; set; }
		[JsonPropertyName("count")] public int Count { get; set; }
	}

	public sealed class MissionCommand
	{
		[JsonPropertyName("schema_version")] public int SchemaVersion { get; set; }
		[JsonPropertyName("session_id")] public string SessionId { get; set; }
		[JsonPropertyName("revision")] public long Revision { get; set; }
		[JsonPropertyName("op")] public string Op { get; set; }
		[JsonPropertyName("observed_tick")] public int ObservedTick { get; set; }
		[JsonPropertyName("mission")] public MissionDefinition Mission { get; set; }
		[JsonPropertyName("mission_id")] public string MissionId { get; set; }
		[JsonPropertyName("reason")] public string Reason { get; set; }
	}

	public sealed class AssignedUnit
	{
		[JsonPropertyName("actor_id")] public uint ActorId { get; set; }
		[JsonPropertyName("semantic_id")] public string SemanticId { get; set; }
		[JsonPropertyName("actor_type")] public string ActorType { get; set; }
	}

	public sealed class MissionRuntime
	{
		[JsonPropertyName("id")] public string Id { get; set; }
		[JsonPropertyName("command_revision")] public long CommandRevision { get; set; }
		[JsonPropertyName("type")] public string Type { get; set; }
		[JsonPropertyName("priority")] public string Priority { get; set; }
		[JsonPropertyName("status")] public string Status { get; set; }
		[JsonPropertyName("ingested_at_tick")] public int IngestedAtTick { get; set; }
		[JsonPropertyName("accepted_at_tick")] public int? AcceptedAtTick { get; set; }
		[JsonPropertyName("started_at_tick")] public int? StartedAtTick { get; set; }
		[JsonPropertyName("completed_at_tick")] public int? CompletedAtTick { get; set; }
		[JsonPropertyName("expires_at_tick")] public int? ExpiresAtTick { get; set; }
		[JsonPropertyName("progress")] public double Progress { get; set; }
		[JsonPropertyName("failure_reason")] public string FailureReason { get; set; }
		[JsonPropertyName("blocker")] public string Blocker { get; set; }
		[JsonPropertyName("blocked_by")] public string BlockedBy { get; set; }
		[JsonPropertyName("blocked_since_tick")] public int? BlockedSinceTick { get; set; }
		[JsonPropertyName("assigned_units")] public List<AssignedUnit> AssignedUnits { get; set; } = [];

		[JsonIgnore] public MissionDefinition Definition { get; set; }
		[JsonIgnore] public Actor TargetActor { get; set; }
		[JsonIgnore] public int InitialActorCount { get; set; }
		[JsonIgnore] public int RequestedProductionCount { get; set; }
	}

	sealed class SavedMission
	{
		public MissionRuntime Runtime { get; set; }
		public MissionDefinition Definition { get; set; }
		public string DefinitionHash { get; set; }
	}

	public sealed class LeaseResult
	{
		public bool Success { get; init; }
		public string Code { get; init; }
		public string Message { get; init; }
		public IReadOnlyCollection<Actor> Units { get; init; } = Array.Empty<Actor>();
	}

	public sealed class DirectedSquadProgress
	{
		public IReadOnlyCollection<Actor> Units { get; init; } = Array.Empty<Actor>();
		public bool TargetAlive { get; init; }
	}

	public sealed class CaptureRequestResult
	{
		public bool Success { get; init; }
		public string Code { get; init; }
		public string Message { get; init; }
		public IReadOnlyCollection<Actor> Units { get; init; } = Array.Empty<Actor>();
	}

	public sealed class CaptureProgress
	{
		public IReadOnlyCollection<Actor> Units { get; init; } = Array.Empty<Actor>();
		public bool TargetOwnedByRequester { get; init; }
		public bool TargetAlive { get; init; }
	}

	public interface IBotRequestDirectedSquad
	{
		LeaseResult TryLeaseDirectedSquad(IBot bot, string missionId, Actor target, int requestedUnits);
		void ReleaseDirectedSquad(IBot bot, string missionId, string reason);
		DirectedSquadProgress GetDirectedSquadProgress(string missionId);
	}

	public interface IBotRequestCaptureMission
	{
		CaptureRequestResult RequestCapture(IBot bot, string missionId, Actor target, string capturerType, int escortUnits);
		CaptureProgress GetCaptureProgress(string missionId);
		void CancelCapture(IBot bot, string missionId);
	}

	public sealed class ProductionRequestResult
	{
		public bool Success { get; init; }
		public string Code { get; init; }
		public string Message { get; init; }
	}

	public interface IBotRequestBuildingProduction
	{
		ProductionRequestResult RequestBuilding(IBot bot, string missionId, string actorType, int count);
		void CancelBuildingRequest(IBot bot, string missionId);
	}

	public interface IBotMissionCoordinator
	{
		MissionDecision Accept(IBot bot, MissionCommand command, int worldTick);
		MissionDecision Cancel(IBot bot, string missionId, int worldTick);
		void MissionTick(IBot bot, int worldTick);
		IReadOnlyCollection<MissionRuntime> Snapshot();
	}

	/// <summary>
	/// File-backed Phase 1b mission coordinator. It is deliberately a thin
	/// control plane: squad, capture and production modules remain responsible
	/// for pathing, orders, prerequisites and game-state mutation.
	/// </summary>
	public sealed class MissionCoordinator : IBotMissionCoordinator, IGameSaveTraitData, INotifyActorDisposing
	{
		const int PollInterval = 5;
		const int StatusHeartbeatInterval = 25;
		static readonly JsonSerializerOptions JsonOptions = new() { WriteIndented = true, DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull };
		readonly World world;
		readonly Player player;
		readonly string sessionId;
		readonly string sessionDirectory;
		readonly string commandsPath;
		readonly string statusPath;
		readonly string auditPath;
		readonly Dictionary<string, MissionRuntime> missions = new(StringComparer.Ordinal);
		readonly Dictionary<string, string> definitionHashes = new(StringComparer.Ordinal);
		static readonly IReadOnlyDictionary<string, (string ActorType, CPos Cell)> AgendaPois = new Dictionary<string, (string, CPos)>(StringComparer.OrdinalIgnoreCase)
		{
			["oil_west_edge"] = ("oilb", new CPos(5, 33)),
			["oil_north_west"] = ("oilb", new CPos(60, 20)),
			["oil_center_west"] = ("oilb", new CPos(57, 36)),
			["oil_north_east"] = ("oilb", new CPos(68, 20)),
			["oil_center_east"] = ("oilb", new CPos(71, 36)),
			["oil_east_edge"] = ("oilb", new CPos(123, 33)),
			["hospital_center"] = ("hosp", new CPos(64, 33)),
			["hospital_south"] = ("hosp", new CPos(64, 68)),
		};
		long lastAppliedRevision;
		int lastStatusWriteTick = -StatusHeartbeatInterval;
		bool disposed;
		string protocolError;

		public MissionCoordinator(Actor self)
		{
			world = self.World;
			player = self.Owner;
			sessionId = self.Owner.PlayerActor.TraitOrDefault<ObservationTrait>()?.SessionId
				?? Environment.GetEnvironmentVariable("RL_SESSION_ID")
				?? string.Empty;
			if (string.IsNullOrWhiteSpace(sessionId) || sessionId != Path.GetFileName(sessionId))
				return;

			var configuredRoot = Environment.GetEnvironmentVariable("RL_MISSION_DIR");
			if (string.IsNullOrWhiteSpace(configuredRoot))
				configuredRoot = Environment.GetEnvironmentVariable("RL_OBSERVATION_DIR");
			var root = string.IsNullOrWhiteSpace(configuredRoot)
				? Path.Combine(Platform.SupportDir, "RLBridge", "runtime")
				: configuredRoot;
			sessionDirectory = Path.Combine(Path.GetFullPath(root), sessionId);
			commandsPath = Path.Combine(sessionDirectory, "mission-commands.jsonl");
			statusPath = Path.Combine(sessionDirectory, "mission-status.json");
			auditPath = Path.Combine(sessionDirectory, "mission-audit.jsonl");
		}

		public IReadOnlyCollection<MissionRuntime> Snapshot() => missions.Values.ToArray();

		public MissionDecision Accept(IBot bot, MissionCommand command, int worldTick)
		{
			if (command == null || command.Mission == null)
				return Reject(MissionDecisionCode.InvalidSchema, "issue requires mission");
			if (command.SchemaVersion != 1)
				return Reject(MissionDecisionCode.InvalidSchema, "unsupported schema_version");
			if (!string.Equals(command.SessionId, sessionId, StringComparison.Ordinal))
				return Reject(MissionDecisionCode.SessionMismatch, "command belongs to another session");
			if (!ValidateDefinition(command.Mission, out var validationError))
				return Reject(validationError.Code, validationError.Message);

			var definition = command.Mission;
			var hash = JsonSerializer.Serialize(definition, JsonOptions);
			if (missions.TryGetValue(definition.Id, out var existing))
			{
				if (definitionHashes[definition.Id] == hash)
					return new MissionDecision { Accepted = true, Code = MissionDecisionCode.Idempotent, Message = "duplicate issue is idempotent" };
				WriteAudit(worldTick, definition.Id, "conflict", "mission id reused with different payload");
				return Reject(MissionDecisionCode.MissionConflict, "mission id already exists with a different payload");
			}

			var runtime = new MissionRuntime
			{
				Id = definition.Id,
				CommandRevision = command.Revision,
				Type = definition.Type,
				Priority = definition.Priority,
				Status = "accepted",
				IngestedAtTick = worldTick,
				AcceptedAtTick = worldTick,
				ExpiresAtTick = worldTick + definition.TtlTicks,
				Progress = 0,
				Definition = definition,
				InitialActorCount = CountOwnedActors(definition.ActorType ?? definition.UnitType),
			};
			runtime.TargetActor = ResolveTarget(definition);
			if ((definition.Type == "attack" || definition.Type == "capture") && runtime.TargetActor == null)
			{
				runtime.Status = "failed";
				runtime.FailureReason = "target_lost";
				runtime.CompletedAtTick = worldTick;
				missions[definition.Id] = runtime;
				definitionHashes[definition.Id] = hash;
				WriteAudit(worldTick, definition.Id, "failed", "target_lost");
				WriteStatus(worldTick, force: true);
				return Reject(MissionDecisionCode.TargetUnknown, "semantic target could not be resolved");
			}

			missions[definition.Id] = runtime;
			definitionHashes[definition.Id] = hash;
			WriteAudit(worldTick, definition.Id, "accepted", null);
			TryArbitrate(bot, worldTick);
			WriteStatus(worldTick, force: true);
			return new MissionDecision { Accepted = true, Code = MissionDecisionCode.Accepted, Message = "mission accepted" };
		}

		public MissionDecision Cancel(IBot bot, string missionId, int worldTick)
		{
			if (string.IsNullOrWhiteSpace(missionId) || !missions.TryGetValue(missionId, out var runtime))
			{
				WriteAudit(worldTick, missionId, "cancel_noop", "mission_not_found");
				return Reject(MissionDecisionCode.MissionNotFound, "mission not found");
			}

			if (IsTerminal(runtime.Status))
				return new MissionDecision { Accepted = true, Code = MissionDecisionCode.Idempotent, Message = "terminal mission already cancelled or completed" };

			Release(bot, runtime, "cancelled");
			WriteAudit(worldTick, runtime.Id, "lease_released", "cancelled");
			Transition(runtime, "cancelled", worldTick, "cancelled");
			WriteAudit(worldTick, runtime.Id, "cancelled", "cancelled");
			TryArbitrate(bot, worldTick);
			WriteStatus(worldTick, force: true);
			return new MissionDecision { Accepted = true, Code = MissionDecisionCode.Accepted, Message = "mission cancelled" };
		}

		public void MissionTick(IBot bot, int worldTick)
		{
			if (disposed)
				return;

			if (worldTick % PollInterval == 0)
				ReadCommandLog(bot, worldTick);

			foreach (var runtime in missions.Values.ToArray())
			{
				if (!IsTerminal(runtime.Status) && runtime.ExpiresAtTick is int expiry && worldTick >= expiry)
				{
					Release(bot, runtime, "expired");
					Transition(runtime, "failed", worldTick, "expired");
					WriteAudit(worldTick, runtime.Id, "failed", "expired");
				}

				if (runtime.Status == "in_progress")
					UpdateProgress(bot, runtime, worldTick);
			}

			TryArbitrate(bot, worldTick);
			if (worldTick - lastStatusWriteTick >= StatusHeartbeatInterval)
				WriteStatus(worldTick, force: false);
		}

		void ReadCommandLog(IBot bot, int worldTick)
		{
			if (string.IsNullOrEmpty(commandsPath) || !File.Exists(commandsPath))
				return;

			string text;
			try { text = File.ReadAllText(commandsPath); }
			catch (Exception e)
			{
				protocolError = "status_unavailable";
				Log.Write("rl-bridge", $"event=mission_command_read_error session={sessionId} error={e.Message}");
				return;
			}

			var lines = text.Split('\n');
			if (!text.EndsWith("\n", StringComparison.Ordinal))
				lines = lines[..^1]; // tolerate a torn final JSONL record

			foreach (var line in lines)
			{
				if (string.IsNullOrWhiteSpace(line))
					continue;
				if (!TryParseCommand(line, out var command, out var error))
				{
					protocolError = error;
					WriteAudit(worldTick, string.Empty, "protocol_error", error);
					break;
				}
				if (!string.Equals(command.SessionId, sessionId, StringComparison.Ordinal))
				{
					protocolError = "session_mismatch";
					WriteAudit(worldTick, command.MissionId ?? command.Mission?.Id, "protocol_error", "session_mismatch");
					break;
				}
				if (command.Revision <= lastAppliedRevision)
					continue;
				if (command.Revision != lastAppliedRevision + 1)
				{
					protocolError = "revision_gap";
					WriteAudit(worldTick, command.MissionId ?? command.Mission?.Id, "revision_gap", $"expected {lastAppliedRevision + 1}, got {command.Revision}");
					break;
				}

				var decision = command.Op == "issue"
					? Accept(bot, command, worldTick)
					: Cancel(bot, command.MissionId, worldTick);
				if (decision.Code == MissionDecisionCode.InvalidSchema || decision.Code == MissionDecisionCode.SessionMismatch || decision.Code == MissionDecisionCode.RevisionGap)
					break;
				lastAppliedRevision = command.Revision;
				// Accept/Cancel may write status while applying the event. Refresh
				// once more after advancing the revision so recovery sees the
				// engine-owned command watermark, not the previous value.
				WriteStatus(worldTick, force: true);
			}
		}

		static bool TryParseCommand(string line, out MissionCommand command, out string error)
		{
			command = null;
			error = null;
			try
			{
				using var document = JsonDocument.Parse(line);
				var root = document.RootElement;
				if (root.ValueKind != JsonValueKind.Object || !root.TryGetProperty("schema_version", out var version) || version.GetInt32() != 1)
				{
					error = "invalid_schema";
					return false;
				}
				var envelopeFields = new HashSet<string>(StringComparer.Ordinal) { "schema_version", "session_id", "revision", "op", "observed_tick", "mission", "mission_id", "reason" };
				if (root.EnumerateObject().Any(p => !envelopeFields.Contains(p.Name)))
				{
					error = "invalid_schema";
					return false;
				}
				command = JsonSerializer.Deserialize<MissionCommand>(line, JsonOptions);
				if (command == null || (command.Op != "issue" && command.Op != "cancel") || string.IsNullOrWhiteSpace(command.SessionId) || command.Revision < 1)
				{
					error = "invalid_schema";
					return false;
				}
				if (command.Op == "issue" && command.Mission == null)
				{
					error = "invalid_schema";
					return false;
				}
				if (command.Op == "issue")
				{
					var missionElement = root.GetProperty("mission");
					var missionFields = new HashSet<string>(StringComparer.Ordinal) { "id", "type", "priority", "ttl_ticks", "notes", "target", "force_units", "unit_type", "escort_units", "actor_type", "count" };
					if (missionElement.ValueKind != JsonValueKind.Object || missionElement.EnumerateObject().Any(p => !missionFields.Contains(p.Name)))
					{
						error = "invalid_schema";
						return false;
					}
					var type = missionElement.TryGetProperty("type", out var typeElement) ? typeElement.GetString() : null;
					var allowedFields = type switch
					{
						"attack" => new HashSet<string>(StringComparer.Ordinal) { "id", "type", "priority", "ttl_ticks", "notes", "target", "force_units" },
						"capture" => new HashSet<string>(StringComparer.Ordinal) { "id", "type", "priority", "ttl_ticks", "notes", "target", "unit_type", "escort_units" },
						"produce" or "build" => new HashSet<string>(StringComparer.Ordinal) { "id", "type", "priority", "ttl_ticks", "notes", "actor_type", "count" },
						_ => null,
					};
					if (allowedFields == null || missionElement.EnumerateObject().Any(p => !allowedFields.Contains(p.Name)))
					{
						error = "invalid_schema";
						return false;
					}
				}
				if (command.Op == "cancel" && string.IsNullOrWhiteSpace(command.MissionId))
				{
					error = "invalid_schema";
					return false;
				}
				return true;
			}
			catch (Exception e)
			{
				error = $"invalid_schema:{e.Message}";
				return false;
			}
		}

		static bool ValidateDefinition(MissionDefinition definition, out (MissionDecisionCode Code, string Message) error)
		{
			error = default;
			if (definition == null || string.IsNullOrWhiteSpace(definition.Id) || definition.Id.Length > 64 ||
				string.IsNullOrWhiteSpace(definition.Type) || !new[] { "attack", "capture", "produce", "build" }.Contains(definition.Type))
			{
				error = (MissionDecisionCode.InvalidSchema, "invalid mission type or id");
				return false;
			}
			if (!new[] { "low", "medium", "high" }.Contains(definition.Priority) || definition.TtlTicks < 25 || definition.TtlTicks > 45000)
			{
				error = (MissionDecisionCode.InvalidSchema, "invalid priority or ttl_ticks");
				return false;
			}
			if ((definition.Type == "attack" || definition.Type == "capture") && string.IsNullOrWhiteSpace(definition.Target))
			{
				error = (MissionDecisionCode.InvalidSchema, "unit mission requires target");
				return false;
			}
			if (definition.Type == "capture" && string.IsNullOrWhiteSpace(definition.UnitType))
			{
				error = (MissionDecisionCode.InvalidSchema, "capture requires unit_type");
				return false;
			}
			if ((definition.Type == "produce" || definition.Type == "build") && (string.IsNullOrWhiteSpace(definition.ActorType) || definition.Count < 1))
			{
				error = (MissionDecisionCode.InvalidSchema, "production mission requires actor_type and count");
				return false;
			}
			return true;
		}

		void TryArbitrate(IBot bot, int worldTick)
		{
			var active = missions.Values.FirstOrDefault(m => m.Status == "in_progress" && IsUnitConsuming(m.Type));
			var next = missions.Values
				.Where(m => (m.Status == "accepted" || m.Status == "blocked") && IsUnitConsuming(m.Type))
				.OrderByDescending(m => Priority(m.Priority)).ThenBy(m => m.CommandRevision)
				.FirstOrDefault();
			if (next == null && active == null)
			{
				var production = missions.Values
					.Where(m => (m.Status == "accepted" || m.Status == "blocked") && !IsUnitConsuming(m.Type))
					.OrderByDescending(m => Priority(m.Priority)).ThenBy(m => m.CommandRevision)
					.FirstOrDefault();
				if (production != null)
					StartMission(bot, production, worldTick);
				return;
			}

			if (next == null)
				return;

			if (active != null)
			{
				if (Priority(next.Priority) <= Priority(active.Priority))
					return;
				Release(bot, active, "preempted");
				WriteAudit(worldTick, active.Id, "lease_released", "preempted");
				active.Status = "blocked";
				active.FailureReason = "preempted";
				active.BlockedBy = next.Id;
				active.BlockedSinceTick = worldTick;
				active.Blocker = "higher_priority_mission";
				WriteAudit(worldTick, active.Id, "blocked", "preempted");
			}

			StartMission(bot, next, worldTick);
		}

		void StartMission(IBot bot, MissionRuntime runtime, int worldTick)
		{
			runtime.TargetActor ??= ResolveTarget(runtime.Definition);
			if (runtime.Type == "attack")
			{
				var squad = player.PlayerActor.TraitsImplementing<IBotRequestDirectedSquad>().FirstOrDefault();
				if (squad == null || runtime.TargetActor == null)
				{
					Block(runtime, worldTick, "no_units", "directed squad unavailable");
					return;
				}
				var result = squad.TryLeaseDirectedSquad(bot, runtime.Id, runtime.TargetActor, Math.Max(3, runtime.Definition.ForceUnits > 0 ? runtime.Definition.ForceUnits : 8));
				if (!result.Success)
				{
					Block(runtime, worldTick, result.Code ?? "no_units", result.Message);
					return;
				}
				Assign(runtime, result.Units);
				Transition(runtime, "in_progress", worldTick, null);
				WriteAudit(worldTick, runtime.Id, "started", "directed_squad_leased");
				return;
			}

			if (runtime.Type == "capture")
			{
				var capture = player.PlayerActor.TraitsImplementing<IBotRequestCaptureMission>().FirstOrDefault();
				if (capture == null || runtime.TargetActor == null)
				{
					Block(runtime, worldTick, "no_units", "capture module unavailable");
					return;
				}
				var result = capture.RequestCapture(bot, runtime.Id, runtime.TargetActor, runtime.Definition.UnitType, runtime.Definition.EscortUnits);
				if (!result.Success)
				{
					var builder = player.PlayerActor.TraitsImplementing<IBotRequestUnitProduction>().FirstOrDefault();
					if (builder != null && result.Code == "no_units")
					{
						builder.RequestUnitProduction(bot, runtime.Definition.UnitType);
						Block(runtime, worldTick, "no_units", "capturer requested from production");
					}
					else
						Block(runtime, worldTick, result.Code ?? "no_units", result.Message);
					return;
				}
				Assign(runtime, result.Units);
				Transition(runtime, "in_progress", worldTick, null);
				WriteAudit(worldTick, runtime.Id, "started", "capture_requested");
				return;
			}

			var requested = runtime.Type == "produce"
				? player.PlayerActor.TraitsImplementing<IBotRequestUnitProduction>().FirstOrDefault()
				: null;
			var building = runtime.Type == "build"
				? player.PlayerActor.TraitsImplementing<IBotRequestBuildingProduction>().FirstOrDefault()
				: null;
			if (runtime.Type == "produce" && requested != null)
			{
				for (var i = 0; i < runtime.Definition.Count; i++)
					requested.RequestUnitProduction(bot, runtime.Definition.ActorType);
				runtime.RequestedProductionCount = runtime.Definition.Count;
				Transition(runtime, "in_progress", worldTick, null);
				WriteAudit(worldTick, runtime.Id, "started", "unit_production_requested");
			}
			else if (runtime.Type == "build" && building != null)
			{
				var result = building.RequestBuilding(bot, runtime.Id, runtime.Definition.ActorType, runtime.Definition.Count);
				if (result.Success)
				{
					runtime.RequestedProductionCount = runtime.Definition.Count;
					Transition(runtime, "in_progress", worldTick, null);
					WriteAudit(worldTick, runtime.Id, "started", "building_production_requested");
				}
				else
					Block(runtime, worldTick, result.Code ?? "no_prereq", result.Message);
			}
			else
				Block(runtime, worldTick, "no_prereq", "requested production interface unavailable");
		}

		void UpdateProgress(IBot bot, MissionRuntime runtime, int worldTick)
		{
			if (runtime.Type == "attack")
			{
				var squad = player.PlayerActor.TraitsImplementing<IBotRequestDirectedSquad>().FirstOrDefault();
				var progress = squad?.GetDirectedSquadProgress(runtime.Id);
				if (progress == null || progress.Units.Count == 0)
				{
					Block(runtime, worldTick, "no_units", "all leased units are gone");
					return;
				}
				runtime.Progress = progress.TargetAlive ? Math.Min(0.99, runtime.Progress + 0.01) : 1;
				if (!progress.TargetAlive)
				{
					Release(bot, runtime, "succeeded");
					Transition(runtime, "succeeded", worldTick, null);
					WriteAudit(worldTick, runtime.Id, "succeeded", "target_destroyed");
				}
			}
			else if (runtime.Type == "capture")
			{
				var capture = player.PlayerActor.TraitsImplementing<IBotRequestCaptureMission>().FirstOrDefault();
				var progress = capture?.GetCaptureProgress(runtime.Id);
				if (progress == null || progress.Units.Count == 0)
				{
					Block(runtime, worldTick, "no_units", "capturer is unavailable");
					return;
				}
				runtime.Progress = progress.TargetOwnedByRequester ? 1 : Math.Min(0.99, runtime.Progress + 0.005);
				if (progress.TargetOwnedByRequester)
				{
					Release(bot, runtime, "succeeded");
					Transition(runtime, "succeeded", worldTick, null);
					WriteAudit(worldTick, runtime.Id, "succeeded", "target_captured");
				}
			}
			else
			{
				var current = CountOwnedActors(runtime.Definition.ActorType);
				 runtime.Progress = Math.Min(1, (double)Math.Max(0, current - runtime.InitialActorCount) / Math.Max(1, runtime.Definition.Count));
				if (current - runtime.InitialActorCount >= runtime.Definition.Count)
				{
					Transition(runtime, "succeeded", worldTick, null);
					WriteAudit(worldTick, runtime.Id, "succeeded", "production_complete");
				}
			}
		}

		void Block(MissionRuntime runtime, int worldTick, string reason, string blocker)
		{
			var changed = runtime.Status != "blocked" || !string.Equals(runtime.FailureReason, reason, StringComparison.Ordinal) || !string.Equals(runtime.Blocker, blocker, StringComparison.Ordinal);
			runtime.Status = "blocked";
			runtime.FailureReason = reason;
			runtime.Blocker = blocker;
			runtime.BlockedSinceTick ??= worldTick;
			if (changed)
				WriteAudit(worldTick, runtime.Id, "blocked", reason);
		}

		void Assign(MissionRuntime runtime, IEnumerable<Actor> units)
		{
			runtime.AssignedUnits = units.Where(a => a != null && !a.IsDead && a.IsInWorld)
				.Select(a => new AssignedUnit { ActorId = a.ActorID, SemanticId = $"own_actor_{a.ActorID}", ActorType = a.Info.Name.ToLowerInvariant() })
				.ToList();
		}

		void Release(IBot bot, MissionRuntime runtime, string reason)
		{
			if (runtime.Type == "attack")
				player.PlayerActor.TraitsImplementing<IBotRequestDirectedSquad>().FirstOrDefault()?.ReleaseDirectedSquad(bot, runtime.Id, reason);
			else if (runtime.Type == "capture")
				player.PlayerActor.TraitsImplementing<IBotRequestCaptureMission>().FirstOrDefault()?.CancelCapture(bot, runtime.Id);
			else if (runtime.Type == "build")
				player.PlayerActor.TraitsImplementing<IBotRequestBuildingProduction>().FirstOrDefault()?.CancelBuildingRequest(bot, runtime.Id);
			runtime.AssignedUnits.Clear();
		}

		static void Transition(MissionRuntime runtime, string status, int tick, string reason)
		{
			runtime.Status = status;
			if (status == "in_progress")
				runtime.StartedAtTick ??= tick;
			if (IsTerminal(status))
			{
				runtime.CompletedAtTick = tick;
				runtime.FailureReason = reason;
			}
		}

		Actor ResolveTarget(MissionDefinition definition)
		{
			if (string.IsNullOrWhiteSpace(definition?.Target))
				return null;
			if (AgendaPois.TryGetValue(definition.Target, out var poi))
			{
				var poiCandidates = world.Actors
					.Where(a => a.IsInWorld && !a.IsDead && string.Equals(a.Info.Name, poi.ActorType, StringComparison.OrdinalIgnoreCase))
					.OrderBy(a => (world.Map.CellContaining(a.CenterPosition) - poi.Cell).LengthSquared)
					.ThenBy(a => a.ActorID);
				var poiActor = poiCandidates.FirstOrDefault(a => world.Map.CellContaining(a.CenterPosition) == poi.Cell) ?? poiCandidates.FirstOrDefault();
				if (poiActor == null)
					return null;
				if (definition.Type == "capture" && poiActor.TraitOrDefault<CaptureManager>() == null)
					return null;
				return poiActor;
			}
			var token = definition.Target.Split('_').LastOrDefault();
			if (!uint.TryParse(token, out var actorId))
				return null;
			var actor = world.GetActorById(actorId);
			if (actor == null || actor.IsDead || !actor.IsInWorld)
				return null;
			if (definition.Type == "attack" && actor.Owner == player)
				return null;
			if (definition.Type == "capture" && actor.TraitOrDefault<CaptureManager>() == null)
				return null;
			return actor;
		}

		int CountOwnedActors(string actorType)
		{
			if (string.IsNullOrWhiteSpace(actorType))
				return 0;
			return world.Actors.Count(a => a.Owner == player && a.IsInWorld && !a.IsDead && string.Equals(a.Info.Name, actorType, StringComparison.OrdinalIgnoreCase));
		}

		void WriteStatus(int worldTick, bool force)
		{
			if (string.IsNullOrEmpty(statusPath) || (!force && worldTick - lastStatusWriteTick < StatusHeartbeatInterval))
				return;
			try
			{
				Directory.CreateDirectory(sessionDirectory);
				var missionStatus = missions.Values.Select(m => new
				{
					id = m.Id,
					command_revision = m.CommandRevision,
					type = m.Type,
					priority = m.Priority,
					status = m.Status,
					ingested_at_tick = m.IngestedAtTick,
					accepted_at_tick = m.AcceptedAtTick,
					started_at_tick = m.StartedAtTick,
					completed_at_tick = m.CompletedAtTick,
					expires_at_tick = m.ExpiresAtTick,
					progress = m.Progress,
					failure_reason = m.FailureReason,
					blocker = m.Blocker,
					blocked_by = m.BlockedBy,
					blocked_since_tick = m.BlockedSinceTick,
					assigned_units = m.AssignedUnits,
				}).ToArray();
				var status = new
				{
					schema_version = 1,
					session_id = sessionId,
					last_applied_revision = lastAppliedRevision,
					generated_at_tick = worldTick,
					missions = missionStatus,
					protocol_error = protocolError,
				};
				WriteAtomically(statusPath, JsonSerializer.SerializeToUtf8Bytes(status, JsonOptions));
				lastStatusWriteTick = worldTick;
			}
			catch (Exception e)
			{
				try
				{
					var fallback = new
					{
						schema_version = 1,
						session_id = sessionId,
						last_applied_revision = lastAppliedRevision,
						generated_at_tick = worldTick,
						missions = Array.Empty<object>(),
						protocol_error = protocolError ?? "status_serialize_error",
					};
					WriteAtomically(statusPath, JsonSerializer.SerializeToUtf8Bytes(fallback, JsonOptions));
				}
				catch
				{
					// Preserve the original write failure in the engine log; cleanup remains idempotent.
				}
				Log.Write("rl-bridge", $"event=mission_status_write_error session={sessionId} error={e.Message}");
			}
		}

		void WriteAudit(int tick, string missionId, string eventName, string reason)
		{
			if (string.IsNullOrEmpty(auditPath))
				return;
			try
			{
				Directory.CreateDirectory(sessionDirectory);
				var record = new { schema_version = 1, session_id = sessionId, tick, mission_id = missionId, @event = eventName, reason };
				var auditOptions = new JsonSerializerOptions { DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull };
				using var stream = new FileStream(auditPath, FileMode.Append, FileAccess.Write, FileShare.ReadWrite);
				using var writer = new StreamWriter(stream);
				writer.WriteLine(JsonSerializer.Serialize(record, auditOptions));
				writer.Flush();
				stream.Flush(true);
			}
			catch (Exception e) { Log.Write("rl-bridge", $"event=mission_audit_write_error session={sessionId} error={e.Message}"); }
		}

		static void WriteAtomically(string destination, byte[] bytes)
		{
			var temporary = destination + $".{Guid.NewGuid():N}.tmp";
			try
			{
				using (var stream = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None))
				{
					stream.Write(bytes, 0, bytes.Length);
					stream.Flush(true);
				}
				File.Move(temporary, destination, true);
			}
			finally { if (File.Exists(temporary)) File.Delete(temporary); }
		}

		static bool IsUnitConsuming(string type) => type is "attack" or "capture" or "defend" or "scout";
		static bool IsTerminal(string status) => status is "succeeded" or "failed" or "cancelled";
		static int Priority(string priority) => priority switch { "high" => 3, "medium" => 2, _ => 1 };
		static MissionDecision Reject(MissionDecisionCode code, string message) => new() { Accepted = false, Code = code, Message = message };

		List<MiniYamlNode> IGameSaveTraitData.IssueTraitData(Actor self)
		{
			if (self.World.IsReplay)
				return null;
			var saved = missions.Values.Select(runtime => new SavedMission
			{
				Runtime = runtime,
				Definition = runtime.Definition,
				DefinitionHash = definitionHashes.TryGetValue(runtime.Id, out var hash) ? hash : null,
			}).ToArray();
			return [
				new("LastAppliedMissionRevision", FieldSaver.FormatValue(lastAppliedRevision)),
				new("MissionRuntime", JsonSerializer.Serialize(saved, JsonOptions)),
			];
		}

		void IGameSaveTraitData.ResolveTraitData(Actor self, MiniYaml data)
		{
			if (self.World.IsReplay)
				return;
			var nodes = data.ToDictionary();
			if (nodes.TryGetValue("LastAppliedMissionRevision", out var revision))
				lastAppliedRevision = FieldLoader.GetValue<long>("LastAppliedMissionRevision", revision.Value);
			if (!nodes.TryGetValue("MissionRuntime", out var savedNode) || string.IsNullOrWhiteSpace(savedNode.Value))
				return;
			try
			{
				var saved = JsonSerializer.Deserialize<SavedMission[]>(savedNode.Value, JsonOptions) ?? Array.Empty<SavedMission>();
				foreach (var entry in saved)
				{
					if (entry?.Runtime == null || string.IsNullOrWhiteSpace(entry.Runtime.Id) || entry.Definition == null)
						continue;
					var runtime = entry.Runtime;
					runtime.Definition = entry.Definition;
					runtime.TargetActor = null;
					runtime.AssignedUnits ??= [];
					// Actor references are world-local. Active leases must be
					// reacquired after load, so resume them as blocked work.
					if (runtime.Status == "in_progress")
					{
						runtime.Status = "blocked";
						runtime.Blocker = "save_load_reacquire";
						runtime.BlockedSinceTick = null;
						runtime.AssignedUnits.Clear();
					}
					missions[runtime.Id] = runtime;
					if (!string.IsNullOrWhiteSpace(entry.DefinitionHash))
						definitionHashes[runtime.Id] = entry.DefinitionHash;
				}
			}
			catch (Exception e)
			{
				protocolError = $"save_load_restore_failed: {e.Message}";
			}
		}

		void INotifyActorDisposing.Disposing(Actor self)
		{
			disposed = true;
			foreach (var runtime in missions.Values.Where(m => !IsTerminal(m.Status)))
				runtime.Status = "failed";
			WriteStatus(self.World.WorldTick, force: true);
			try
			{
				if (!string.IsNullOrEmpty(sessionDirectory) && Directory.Exists(sessionDirectory))
				{
					foreach (var path in new[] { commandsPath, statusPath, auditPath })
						if (File.Exists(path)) File.Delete(path);
				}
			}
			catch (Exception e) { Log.Write("rl-bridge", $"event=mission_cleanup_error session={sessionId} error={e.Message}"); }
		}
	}
}
