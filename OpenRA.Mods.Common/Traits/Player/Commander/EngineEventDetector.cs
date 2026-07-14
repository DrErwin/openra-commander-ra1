#region Copyright & License Information
/*
 * Copyright (c) The OpenRA Developers and Contributors
 * This file is part of OpenRA, which is free software under the GPL-3.0.
 */
#endregion

using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using OpenRA.Support;
using OpenRA.Traits;

namespace OpenRA.Mods.Common.Traits.Commander
{
	[TraitLocation(SystemActors.Player)]
	public sealed class EngineEventDetectorInfo : ConditionalTraitInfo
	{
		[Desc("Emit reliable, low-level engine events for the semantic observation bridge.")]
		public readonly bool Enabled = true;

		public override object Create(ActorInitializer init) { return new EngineEventDetector(this, init); }
	}

	/// <summary>
	/// Read-only event detector. It compares the visible/owned actor set at each
	/// observation heartbeat and appends a session-scoped JSONL sidecar. It does
	/// not issue orders or make semantic/LLM decisions.
	/// </summary>
	public sealed class EngineEventDetector : ConditionalTrait<EngineEventDetectorInfo>, ITick, INotifyActorDisposing
	{
		readonly World world;
		string sessionId;
		string eventPath;
		readonly Player player;
		readonly HashSet<uint> previousOwnActors = new();
		readonly HashSet<uint> previousOwnBuildings = new();
		readonly HashSet<uint> previousVisibleEnemies = new();
		readonly HashSet<uint> previousVisibleEnemyBuildings = new();
		readonly Dictionary<uint, string> previousProduction = new();
		readonly Dictionary<uint, int> previousOwnHp = new();
		readonly Dictionary<string, int> lastEventTick = new();
		bool initialized;
		bool disposed;

		public EngineEventDetector(EngineEventDetectorInfo info, ActorInitializer init)
			: base(info)
		{
			world = init.World;
			player = init.Self.Owner;
			// PlayerActor is assigned only after all player traits have been
			// constructed. Resolve the observation session lazily instead of
			// dereferencing player.PlayerActor during construction.
			sessionId = Environment.GetEnvironmentVariable("RL_SESSION_ID") ?? string.Empty;
			eventPath = string.Empty;
		}

		void EnsureEventPath()
		{
			if (!string.IsNullOrEmpty(eventPath))
				return;

			var observation = player?.PlayerActor?.TraitOrDefault<ObservationTrait>()
				?? ObservationSessionRegistry.Lookup(string.Empty) as ObservationTrait;
			if (observation != null)
				sessionId = observation.SessionId;
			if (string.IsNullOrWhiteSpace(sessionId))
				return;

			var configuredRoot = Environment.GetEnvironmentVariable("RL_EVENT_DIR")
				?? Environment.GetEnvironmentVariable("RL_OBSERVATION_DIR");
			if (string.IsNullOrWhiteSpace(configuredRoot))
				configuredRoot = Path.Combine(Platform.SupportDir, "RLBridge", "runtime");
			eventPath = Path.Combine(Path.GetFullPath(configuredRoot), sessionId, "low-level-events.jsonl");
		}

		void ITick.Tick(Actor self)
		{
			EnsureEventPath();
			if (disposed || IsTraitDisabled || string.IsNullOrEmpty(eventPath) || world.IsLoadingGameSave || world.WorldTick % 10 != 0)
				return;

			var own = new HashSet<uint>();
			var ownBuildings = new HashSet<uint>();
			var visibleEnemies = new HashSet<uint>();
			var visibleEnemyBuildings = new HashSet<uint>();
			var production = new Dictionary<uint, string>();
			var ownHp = new Dictionary<uint, int>();
			foreach (var actor in world.Actors)
			{
				if (actor == world.WorldActor || actor == player.PlayerActor || actor.IsDead || !actor.IsInWorld || actor.Owner == null)
					continue;
				if (actor.Owner == player)
				{
					own.Add((uint)actor.ActorID);
					if (actor.Info.HasTraitInfo<BuildingInfo>()) ownBuildings.Add((uint)actor.ActorID);
					var health = actor.TraitOrDefault<Health>();
					if (health != null) ownHp[(uint)actor.ActorID] = health.HP;
					foreach (var queue in actor.TraitsImplementing<ProductionQueue>())
					{
						var item = queue.CurrentItem();
						if (item != null)
							production[(uint)actor.ActorID] = item.Item;
					}
				}
				// Some maps/launch paths do not have the player's shroud trait ready
				// during the first observation heartbeat. Event detection is optional
				// telemetry and must never terminate the game loop in that window.
				else if (!actor.Owner.NonCombatant && player.Shroud != null && actor.OccupiesSpace != null && player.Shroud.IsVisible(actor.CenterPosition))
				{
					if (actor.Info.HasTraitInfo<BuildingInfo>()) visibleEnemyBuildings.Add((uint)actor.ActorID);
					else visibleEnemies.Add((uint)actor.ActorID);
				}
			}

			if (!initialized)
			{
				initialized = true;
				foreach (var actorId in visibleEnemies) Emit("enemy_spotted", actorId, null, "initial_snapshot");
				foreach (var actorId in visibleEnemyBuildings) Emit("building_discovered", actorId, null, "initial_snapshot");
			}
			else
			{
				foreach (var actorId in previousOwnActors.Except(own))
					Emit(previousOwnBuildings.Contains(actorId) ? "own_building_destroyed" : "unit_destroyed", actorId, null, "actor_missing");
				foreach (var actorId in visibleEnemies.Except(previousVisibleEnemies)) Emit("enemy_spotted", actorId, null, "visible");
				foreach (var actorId in visibleEnemyBuildings.Except(previousVisibleEnemyBuildings)) Emit("building_discovered", actorId, null, "visible");
				foreach (var pair in production)
					if (previousProduction.TryGetValue(pair.Key, out var previous) && !string.Equals(previous, pair.Value, StringComparison.Ordinal))
						Emit("production_complete", pair.Key, null, pair.Value);
				foreach (var pair in ownHp)
					if (previousOwnHp.TryGetValue(pair.Key, out var previousHp) && pair.Value < previousHp)
						Emit("under_attack", pair.Key, null, $"hp:{previousHp}->{pair.Value}");
			}

			previousOwnActors.Clear();
			previousOwnActors.UnionWith(own);
			previousOwnBuildings.Clear();
			previousOwnBuildings.UnionWith(ownBuildings);
			previousVisibleEnemies.Clear();
			previousVisibleEnemies.UnionWith(visibleEnemies);
			previousVisibleEnemyBuildings.Clear();
			previousVisibleEnemyBuildings.UnionWith(visibleEnemyBuildings);
			previousProduction.Clear();
			foreach (var pair in production) previousProduction[pair.Key] = pair.Value;
			previousOwnHp.Clear();
			foreach (var pair in ownHp) previousOwnHp[pair.Key] = pair.Value;
		}

		void Emit(string type, uint actorId, uint? targetActorId, string detail)
		{
			var dedupeKey = $"{type}:{actorId}:{targetActorId?.ToString() ?? ""}";
			if (lastEventTick.TryGetValue(dedupeKey, out var lastTick) && world.WorldTick - lastTick < 25)
				return;
			lastEventTick[dedupeKey] = world.WorldTick;
			try
			{
				var zone = "unknown";
				var actor = world.Actors.FirstOrDefault(a => a != null && (uint)a.ActorID == actorId);
				if (actor != null)
				{
					var cell = world.Map.CellContaining(actor.CenterPosition);
					zone = cell.X < 48 ? "west_lane" : cell.X > 80 ? "east_lane" : cell.Y < 28 ? "north_center" : cell.Y > 49 ? "south_center" : "center";
				}
				var json = JsonSerializer.Serialize(new
				{
					schema_version = 1,
					event_id = $"{sessionId}:{world.WorldTick}:{type}:{actorId}",
					session_id = sessionId,
					tick = world.WorldTick,
					type,
					actor_id = actorId,
					target_actor_id = targetActorId,
					zone_hint = zone,
					detail
				});
				Directory.CreateDirectory(Path.GetDirectoryName(eventPath));
				using var stream = new FileStream(eventPath, FileMode.Append, FileAccess.Write, FileShare.Read);
				using var writer = new StreamWriter(stream);
				writer.WriteLine(json);
				writer.Flush();
				stream.Flush(flushToDisk: true);
			}
			catch (Exception e)
			{
				Log.Write("rl-bridge", $"event=low_level_event_write_error session={sessionId} error={e.Message}");
			}
		}

		void INotifyActorDisposing.Disposing(Actor self)
		{
			disposed = true;
			try
			{
				if (!string.IsNullOrEmpty(eventPath) && File.Exists(eventPath))
					File.Delete(eventPath);
			}
			catch (Exception e)
			{
				Log.Write("rl-bridge", $"event=low_level_event_cleanup_error session={sessionId} error={e.Message}");
			}
		}
	}
}
