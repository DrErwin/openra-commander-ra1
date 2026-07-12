#region Copyright & License Information
/*
 * Copyright (c) The OpenRA Developers and Contributors
 * This file is part of OpenRA, which is free software. It is made
 * available to you under the terms of the GNU General Public License
 * as published by the Free Software Foundation, either version 3 of
 * the License, or (at your option) any later version. For more
 * information, see COPYING.
 */
#endregion

using System;
using System.Threading;
using System.Threading.Channels;
using OpenRA.Traits;
using RLProto = OpenRA.Mods.Common.RL;

namespace OpenRA.Mods.Common.Traits
{
	[TraitLocation(SystemActors.Player)]
	public sealed class ObservationTraitInfo : ConditionalTraitInfo
	{
		[Desc("gRPC port when this trait runs in the legacy single-session host.")]
		public readonly int Port = 9999;

		[Desc("Observation period in simulation ticks.")]
		public readonly int ObservationInterval = 10;

		public override object Create(ActorInitializer init) { return new ObservationTrait(this, init); }
	}

	/// <summary>
	/// Read-only observation endpoint for an autonomous bot.  It never queues
	/// an order and therefore cannot alter normal ModularBot behavior.
	/// </summary>
	public sealed class ObservationTrait : ConditionalTrait<ObservationTraitInfo>, ITick, INotifyActorDisposing, IObservationSession
	{
		readonly World world;
		readonly string sessionId;
		readonly ObservationFileStore fileStore;
		readonly Channel<RLProto.GameObservation> observations = Channel.CreateBounded<RLProto.GameObservation>(
			new BoundedChannelOptions(1)
			{
				FullMode = BoundedChannelFullMode.DropOldest,
				SingleWriter = true,
				SingleReader = false,
			});

		Player player;
		ObservationSerializer serializer;
		int observerCount;
		bool deactivated;
		long observationSequence;
		DateTime lastObserverActivityUtc = DateTime.UtcNow;
		int effectiveObservationInterval;
		int slowSerializationStreak;
		int fastSerializationStreak;

		public ObservationTrait(ObservationTraitInfo info, ActorInitializer init)
			: base(info)
		{
			world = init.World;
			sessionId = ObservationSessionRegistry.MultiSessionMode && !string.IsNullOrEmpty(ObservationSessionRegistry.NextSessionId)
				? ObservationSessionRegistry.NextSessionId
				: Guid.NewGuid().ToString("N")[..12];
			fileStore = new ObservationFileStore(sessionId);
			effectiveObservationInterval = Math.Max(1, info.ObservationInterval);
		}

		public string SessionId => sessionId;
		public bool IsEnabled => !IsTraitDisabled && !deactivated;
		public int ObserverCount => Volatile.Read(ref observerCount);
		public DateTime LastObserverActivityUtc => lastObserverActivityUtc;
		public bool IsGameOver => world.IsGameOver;
		public ChannelReader<RLProto.GameObservation> ObservationReader => observations.Reader;

		protected override void TraitEnabled(Actor self)
		{
			base.TraitEnabled(self);
			player = self.Owner;
			serializer = new ObservationSerializer(world, player, sessionId);
			if (!ObservationSessionRegistry.TryRegister(this))
			{
				fileStore.Delete();
				Log.Write("rl-bridge", $"event=duplicate_observation_session session={sessionId} multi_session={ObservationSessionRegistry.MultiSessionMode}");
				throw new InvalidOperationException($"Duplicate observation session id '{sessionId}'.");
			}

			Log.Write("rl-bridge", $"event=observation_enabled session={sessionId} player={player.InternalName} tick={world.WorldTick} interval={Info.ObservationInterval}");
			if (!ObservationSessionRegistry.MultiSessionMode)
			{
				var thread = new Thread(() => ExternalBotBridge.StartGrpcServer(Info.Port))
				{
					IsBackground = true,
					Name = "Agent-Observation-gRPC"
				};
				thread.Start();
			}
		}

		void ITick.Tick(Actor self)
		{
			if (!IsEnabled || self.World.IsLoadingGameSave || serializer == null)
				return;

			var interval = Volatile.Read(ref effectiveObservationInterval);
			if (world.WorldTick % interval != 0)
				return;

			try
			{
				var stopwatch = System.Diagnostics.Stopwatch.StartNew();
				var observation = serializer.Serialize(world.WorldTick);
				stopwatch.Stop();

				observation.ObservationSequence = (ulong)Interlocked.Increment(ref observationSequence);
				observation.ObservedAtUnixMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
				observation.SerializationMs = (int)Math.Min(int.MaxValue, stopwatch.ElapsedMilliseconds);
				observation.SchemaVersion = "1";
				try
				{
					fileStore.Publish(observation);
				}
				catch (Exception e)
				{
					Log.Write("rl-bridge", $"event=observation_file_publish_error session={sessionId} error={e.Message}");
				}
				observations.Writer.TryWrite(observation);
				UpdateSerializationPolicy(observation.SerializationMs);
			}
			catch (Exception e)
			{
				Log.Write("rl-bridge", $"event=observation_serialize_error session={sessionId} tick={world.WorldTick} error={e.Message}");
			}
		}

		public RLProto.GameState GetCurrentState()
		{
			return ObservationSessionState.Snapshot(world, player, sessionId, IsEnabled);
		}

		public void OnObserverConnected()
		{
			Interlocked.Increment(ref observerCount);
			lastObserverActivityUtc = DateTime.UtcNow;
			Log.Write("rl-bridge", $"event=observer_connected session={sessionId} observers={observerCount}");
		}

		public void OnObserverDisconnected()
		{
			var remaining = Math.Max(0, Interlocked.Decrement(ref observerCount));
			lastObserverActivityUtc = DateTime.UtcNow;
			Log.Write("rl-bridge", $"event=observer_disconnected session={sessionId} observers={remaining}");
		}

		public void Deactivate()
		{
			if (deactivated)
				return;

			deactivated = true;
			ObservationSessionRegistry.Remove(sessionId);
			observations.Writer.TryComplete();
			try
			{
				fileStore.Delete();
			}
			catch (Exception e)
			{
				Log.Write("rl-bridge", $"event=observation_file_cleanup_error session={sessionId} error={e.Message}");
			}
			Log.Write("rl-bridge", $"event=observation_deactivated session={sessionId} tick={world.WorldTick}");
		}

		void UpdateSerializationPolicy(long serializationMs)
		{
			if (serializationMs > 20)
			{
				Log.Write("rl-bridge", $"event=observation_slow session={sessionId} serialization_ms={serializationMs} interval={effectiveObservationInterval}");
				slowSerializationStreak++;
				fastSerializationStreak = 0;
				if (slowSerializationStreak >= 5 && effectiveObservationInterval < 25)
				{
					effectiveObservationInterval = 25;
					Log.Write("rl-bridge", $"event=observation_throttled session={sessionId} interval=25 serialization_ms={serializationMs}");
				}
			}
			else if (serializationMs <= 15)
			{
				fastSerializationStreak++;
				slowSerializationStreak = 0;
				if (fastSerializationStreak >= 10 && effectiveObservationInterval != Math.Max(1, Info.ObservationInterval))
				{
					effectiveObservationInterval = Math.Max(1, Info.ObservationInterval);
					Log.Write("rl-bridge", $"event=observation_throttle_recovered session={sessionId} interval={effectiveObservationInterval}");
				}
			}
			else
			{
				slowSerializationStreak = 0;
				fastSerializationStreak = 0;
			}
		}

		void INotifyActorDisposing.Disposing(Actor self) { Deactivate(); }
	}
}
