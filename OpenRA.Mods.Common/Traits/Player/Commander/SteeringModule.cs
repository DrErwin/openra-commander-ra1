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

	public sealed class SteeringModule : ConditionalTrait<SteeringModuleInfo>, IBotTick
	{
		Player player;

		public SteeringModule(SteeringModuleInfo info)
			: base(info) { }

		protected override void TraitEnabled(Actor self)
		{
			base.TraitEnabled(self);
			Log.Write("rl-bridge", $"event=steering_enabled player={self.Owner.InternalName} tick={self.World.WorldTick}");
		}

		void IBotTick.BotTick(IBot bot)
		{
			player ??= bot.Player;
			if (IsTraitDisabled || player == null || Info.HeartbeatInterval <= 0)
				return;

			if (player.World.WorldTick % Info.HeartbeatInterval == 0)
				Log.Write("rl-bridge", $"event=steering_heartbeat player={player.InternalName} tick={player.World.WorldTick}");
		}
	}
}
