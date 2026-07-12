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

using System.Linq;
using RLProto = OpenRA.Mods.Common.RL;

namespace OpenRA.Mods.Common.Traits
{
	static class ObservationSessionState
	{
		public static RLProto.GameState Snapshot(World world, Player player, string sessionId, bool enabled)
		{
			var state = new RLProto.GameState
			{
				EpisodeId = sessionId,
				Tick = world.WorldTick,
				PlayerCount = world.Players.Count(),
				Phase = !enabled ? "inactive" : world.IsGameOver ? "game_over" : "playing"
			};

			if (player != null)
			{
				state.PlayerFaction = player.Faction.InternalName;
				state.PlayerSpawn = player.SpawnPoint;
			}

			var enemy = world.Players.FirstOrDefault(p => p != player && !p.NonCombatant);
			if (enemy != null)
			{
				state.EnemyFaction = enemy.Faction.InternalName;
				state.EnemySpawn = enemy.SpawnPoint;
			}

			if (world.IsGameOver)
				state.Winner = world.Players.FirstOrDefault(p => p.WinState == WinState.Won)?.InternalName ?? "";

			return state;
		}
	}
}
