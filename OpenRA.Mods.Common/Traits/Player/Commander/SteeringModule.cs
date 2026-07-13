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

using System.Collections.Generic;
using System.Threading;

using OpenRA.Support;
using OpenRA.Traits;

namespace OpenRA.Mods.Common.Traits.Commander
{
	/// <summary>
	/// Phase 1 steering boundary. It deliberately issues no orders: normal
	/// ModularBot remains autonomous until mission control is introduced in 1b.
	/// </summary>
	[TraitLocation(SystemActors.Player)]
	public sealed class SteeringModuleInfo : ConditionalTraitInfo
	{
		[Desc("Ticks between liveness log records.")]
		public readonly int HeartbeatInterval = 250;

		public override object Create(ActorInitializer init) { return new SteeringModule(this); }
	}

	public sealed class SteeringModule : ConditionalTrait<SteeringModuleInfo>, IBotTick, IGameSaveTraitData, INotifyActorDisposing
	{
		Player player;
		MissionCoordinator missionCoordinator;

		// Phase 1 intentionally has no order emission path. The runtime counter
		// is kept for the Gate evidence and has no incrementing call site.
		int issuedOrderCount;

		public SteeringModule(SteeringModuleInfo info)
			: base(info) { }

		protected override void TraitEnabled(Actor self)
		{
			base.TraitEnabled(self);
			missionCoordinator = new MissionCoordinator(self);
			Log.Write("rl-bridge", $"event=steering_enabled player={self.Owner.InternalName} tick={self.World.WorldTick}");
		}

		void IBotTick.BotTick(IBot bot)
		{
			player ??= bot.Player;
			if (IsTraitDisabled || player == null || Info.HeartbeatInterval <= 0)
				return;

			if (player.World.WorldTick % Info.HeartbeatInterval == 0)
			{
				var orderCount = Volatile.Read(ref issuedOrderCount);
				Log.Write("rl-bridge", $"event=steering_heartbeat player={player.InternalName} "
					+ $"tick={player.World.WorldTick} steering_order_count={orderCount}");
			}

			missionCoordinator?.MissionTick(bot, player.World.WorldTick);
		}

		List<MiniYamlNode> IGameSaveTraitData.IssueTraitData(Actor self) =>
			missionCoordinator == null ? null : ((IGameSaveTraitData)missionCoordinator).IssueTraitData(self);

		void IGameSaveTraitData.ResolveTraitData(Actor self, MiniYaml data) =>
			((IGameSaveTraitData)missionCoordinator)?.ResolveTraitData(self, data);

		void INotifyActorDisposing.Disposing(Actor self) =>
			((INotifyActorDisposing)missionCoordinator)?.Disposing(self);
	}
}
