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
using System.Linq;
using OpenRA.Mods.Common.Pathfinder;
using OpenRA.Traits;
using RLProto = OpenRA.Mods.Common.RL;

namespace OpenRA.Mods.Common.Traits
{
	/// <summary>
	/// Serializes the current game state visible to a player into a protobuf GameObservation.
	/// </summary>
	public sealed class ObservationSerializer
	{
		const int SpatialChannelCount = 9;

		readonly World world;
		readonly Player player;
		readonly string episodeId;

		// Cached trait references for spatial map (resolved lazily)
		IResourceLayer resourceLayer;
		BuildingInfluence buildingInfluence;
		Locomotor locomotor;
		bool traitsCached;
		float[] staticSpatialMap;

		public ObservationSerializer(World world, Player player, string episodeId)
		{
			this.world = world;
			this.player = player;
			this.episodeId = episodeId;
		}

		void EnsureTraitsCached()
		{
			if (traitsCached)
				return;

			traitsCached = true;
			resourceLayer = world.WorldActor.TraitOrDefault<IResourceLayer>();
			buildingInfluence = world.WorldActor.TraitOrDefault<BuildingInfluence>();

			// Use first available locomotor for passability checks
			locomotor = world.WorldActor.TraitsImplementing<Locomotor>().FirstOrDefault();
		}

		void EnsureStaticSpatialMap()
		{
			if (staticSpatialMap != null)
				return;

			EnsureTraitsCached();
			var map = world.Map;
			var width = map.MapSize.Width;
			var height = map.MapSize.Height;
			staticSpatialMap = new float[height * width * SpatialChannelCount];
			foreach (var cell in map.AllCells)
			{
				var x = cell.X;
				var y = cell.Y;
				if (x < 0 || x >= width || y < 0 || y >= height)
					continue;

				var baseIdx = (y * width + x) * SpatialChannelCount;
				staticSpatialMap[baseIdx] = map.GetTerrainIndex(cell);
				staticSpatialMap[baseIdx + 1] = map.Height[cell];
				if (locomotor != null)
				{
					var cost = locomotor.MovementCostForCell(cell);
					staticSpatialMap[baseIdx + 3] = cost >= PathGraph.MovementCostForUnreachableCell ? 0f : 1f;
				}
			}
		}

		public RLProto.GameObservation Serialize(int tick)
		{
			var obs = new RLProto.GameObservation
			{
				Tick = tick,
				EpisodeId = episodeId,
				Economy = SerializeEconomy(),
				Military = SerializeMilitary(),
				MapInfo = SerializeMapInfo(),
				Done = world.IsGameOver,
			};

			if (world.IsGameOver)
			{
				obs.Result = player.WinState == WinState.Won ? "win"
					: player.WinState == WinState.Lost ? "lose"
					: "draw";
			}

			SerializeOwnedActors(obs);
			SerializeVisibleEnemies(obs);
			SerializeProduction(obs);
			SerializeSpatialMap(obs);

			return obs;
		}

		RLProto.RlEconomy SerializeEconomy()
		{
			var economy = new RLProto.RlEconomy();

			var resources = player.PlayerActor.TraitOrDefault<PlayerResources>();
			if (resources != null)
			{
				economy.Cash = resources.Cash;
				economy.Ore = resources.Resources;
				economy.ResourceCapacity = resources.ResourceCapacity;
			}

			var power = player.PlayerActor.TraitOrDefault<PowerManager>();
			if (power != null)
			{
				economy.PowerProvided = power.PowerProvided;
				economy.PowerDrained = power.PowerDrained;
			}

			// Count harvesters
			economy.HarvesterCount = world.ActorsHavingTrait<Harvester>()
				.Count(a => a.Owner == player && !a.IsDead && a.IsInWorld);

			return economy;
		}

		RLProto.RlMilitary SerializeMilitary()
		{
			var military = new RLProto.RlMilitary();

			var stats = player.PlayerActor.TraitOrDefault<PlayerStatistics>();
			if (stats != null)
			{
				military.UnitsKilled = stats.UnitsKilled;
				military.UnitsLost = stats.UnitsDead;
				military.BuildingsKilled = stats.BuildingsKilled;
				military.BuildingsLost = stats.BuildingsDead;
				military.KillsCost = stats.KillsCost;
				military.DeathsCost = stats.DeathsCost;
				military.AssetsValue = stats.AssetsValue;
				military.Experience = stats.Experience;
				military.OrderCount = stats.OrderCount;
			}

			// Calculate army value and active unit count
			var ownedActors = world.ActorsHavingTrait<Health>()
				.Where(a => a.Owner == player && !a.IsDead && a.IsInWorld);

			var armyValue = 0;
			var unitCount = 0;
			foreach (var actor in ownedActors)
			{
				if (actor.Info.HasTraitInfo<BuildingInfo>())
					continue;

				unitCount++;

				var valued = actor.Info.TraitInfoOrDefault<ValuedInfo>();
				if (valued != null)
					armyValue += valued.Cost;
			}

			military.ArmyValue = armyValue;
			military.ActiveUnitCount = unitCount;

			return military;
		}

		void SerializeOwnedActors(RLProto.GameObservation obs)
		{
			foreach (var actor in world.Actors)
			{
				if (actor.Owner != player || actor.IsDead || !actor.IsInWorld)
					continue;

				// Skip world/player actors
				if (actor == world.WorldActor || actor == player.PlayerActor)
					continue;

				Health health;
				float hpPercent;
				try
				{
					health = actor.TraitOrDefault<Health>();
					hpPercent = health != null ? (float)health.HP / health.MaxHP : 1f;

					// Test position access early to catch transitional actors
					_ = actor.CenterPosition;
				}
				catch (NullReferenceException)
				{
					continue;
				}

				if (actor.Info.HasTraitInfo<BuildingInfo>())
				{
					var bldg = SerializeBuilding(actor, hpPercent, player.InternalName);
					obs.Buildings.Add(bldg);
				}
				else
				{
					var unit = SerializeUnit(actor, hpPercent, player.InternalName);
					obs.Units.Add(unit);
				}
			}
		}

		void SerializeVisibleEnemies(RLProto.GameObservation obs)
		{
			var enemyCount = 0;
			var visibleCount = 0;
			var buildingCount = 0;
			foreach (var actor in world.Actors)
			{
				if (actor.Owner == player || actor.Owner.NonCombatant
					|| actor.IsDead || !actor.IsInWorld)
					continue;

				if (actor == world.WorldActor)
					continue;

				enemyCount++;
				try
				{
					var isBuilding = actor.Info.HasTraitInfo<BuildingInfo>();
					if (isBuilding) buildingCount++;
					// Check if visible through shroud/fog
					if (player.Shroud.IsVisible(actor.CenterPosition))
					{
						var health = actor.TraitOrDefault<Health>();
						var hpPercent = health != null ? (float)health.HP / health.MaxHP : 1f;

						if (actor.Info.HasTraitInfo<BuildingInfo>())
						{
							var bldg = SerializeBuilding(actor, hpPercent, actor.Owner.InternalName);
							obs.VisibleEnemyBuildings.Add(bldg);
						}
						else
						{
							var unit = SerializeUnit(actor, hpPercent, actor.Owner.InternalName);
							obs.VisibleEnemies.Add(unit);
						}
					}
				}
				catch (NullReferenceException)
				{
					// Actor may be in a transitional state — skip it
				}
			}
			if (obs.Tick % 500 == 0 || obs.Tick < 10)
				Log.Write("rl-bridge", $"Tick {obs.Tick}: {enemyCount} enemy actors, {buildingCount} buildings, {obs.VisibleEnemyBuildings.Count} visible bldgs");
		}

		RLProto.RlBuildingInfo SerializeBuilding(Actor actor, float hpPercent, string owner)
		{
			var bldg = new RLProto.RlBuildingInfo
			{
				ActorId = actor.ActorID,
				Type = actor.Info.Name,
				PosX = actor.CenterPosition.X,
				PosY = actor.CenterPosition.Y,
				HpPercent = hpPercent,
				Owner = owner,
			};

			// Cell position
			var bldgCell = world.Map.CellContaining(actor.CenterPosition);
			bldg.CellX = bldgCell.X;
			bldg.CellY = bldgCell.Y;

			// Power
			var powerTrait = actor.TraitOrDefault<Power>();
			bldg.IsPowered = powerTrait == null || !powerTrait.IsTraitDisabled;
			if (powerTrait != null)
				bldg.PowerAmount = powerTrait.GetEnabledPower();

			// Repair status
			var repair = actor.TraitOrDefault<RepairableBuilding>();
			bldg.IsRepairing = repair != null && repair.RepairActive;

			// Sell value
			var valued = actor.Info.TraitInfoOrDefault<ValuedInfo>();
			var sellable = actor.Info.TraitInfoOrDefault<SellableInfo>();
			if (valued != null && sellable != null)
				bldg.SellValue = valued.Cost * sellable.RefundPercent / 100;

			// Rally point
			var rally = actor.TraitOrDefault<RallyPoint>();
			if (rally != null && rally.Path.Count > 0)
			{
				bldg.RallyX = rally.Path[0].X;
				bldg.RallyY = rally.Path[0].Y;
			}
			else
			{
				bldg.RallyX = -1;
				bldg.RallyY = -1;
			}

			// Production status and capabilities
			var queues = actor.TraitsImplementing<ProductionQueue>();
			foreach (var queue in queues)
			{
				var current = queue.CurrentItem();
				if (current != null && !bldg.IsProducing)
				{
					bldg.IsProducing = true;
					bldg.ProducingItem = current.Item;
					bldg.ProductionProgress = current.TotalTime > 0
						? 1f - (float)current.RemainingTime / current.TotalTime
						: 0f;
				}

				foreach (var buildable in queue.BuildableItems())
					bldg.CanProduce.Add(buildable.Name);
			}

			return bldg;
		}

		RLProto.RlUnitInfo SerializeUnit(Actor actor, float hpPercent, string owner)
		{
			var unit = new RLProto.RlUnitInfo
			{
				ActorId = actor.ActorID,
				Type = actor.Info.Name,
				PosX = actor.CenterPosition.X,
				PosY = actor.CenterPosition.Y,
				HpPercent = hpPercent,
				IsIdle = actor.IsIdle,
				Owner = owner,
				Ammo = -1,
				PassengerCount = -1,
			};

			// Cell position
			var cell = world.Map.CellContaining(actor.CenterPosition);
			unit.CellX = cell.X;
			unit.CellY = cell.Y;

			// Current activity
			var activity = actor.CurrentActivity;
			if (activity != null)
				unit.CurrentActivity = activity.GetType().Name;

			// Attack capability and range
			unit.CanAttack = actor.Info.HasTraitInfo<AttackBaseInfo>();
			var attack = actor.TraitOrDefault<AttackBase>();
			if (attack != null)
				unit.AttackRange = attack.GetMaximumRange().Length;

			// Ammo
			var ammoPool = actor.TraitOrDefault<AmmoPool>();
			if (ammoPool != null)
				unit.Ammo = ammoPool.CurrentAmmoCount;

			// Facing
			var facing = actor.TraitOrDefault<IFacing>();
			if (facing != null)
				unit.Facing = facing.Facing.Angle;

			// Veterancy
			var exp = actor.TraitOrDefault<GainsExperience>();
			if (exp != null)
				unit.ExperienceLevel = exp.Level;

			// Stance
			var autoTarget = actor.TraitOrDefault<AutoTarget>();
			if (autoTarget != null)
				unit.Stance = (int)autoTarget.Stance;

			// Speed (base speed from trait info)
			var mobileInfo = actor.Info.TraitInfoOrDefault<MobileInfo>();
			if (mobileInfo != null)
				unit.Speed = mobileInfo.Speed;

			// Cargo
			var cargo = actor.TraitOrDefault<Cargo>();
			if (cargo != null)
				unit.PassengerCount = cargo.PassengerCount;

			// Building flag (for distinguishing in mixed lists)
			unit.IsBuilding = actor.Info.HasTraitInfo<BuildingInfo>();

			return unit;
		}

		void SerializeProduction(RLProto.GameObservation obs)
		{
			// Collect production from all owned production structures
			foreach (var actor in world.ActorsHavingTrait<ProductionQueue>())
			{
				if (actor.Owner != player || actor.IsDead || !actor.IsInWorld)
					continue;

				foreach (var queue in actor.TraitsImplementing<ProductionQueue>())
				{
					foreach (var item in queue.AllQueued())
					{
						obs.Production.Add(new RLProto.RlProductionInfo
						{
							QueueType = queue.Info.Type,
							Item = item.Item,
							Progress = item.TotalTime > 0
								? 1f - (float)item.RemainingTime / item.TotalTime
								: 0f,
							RemainingTicks = item.RemainingTime,
							RemainingCost = item.RemainingCost,
							Paused = item.Paused,
						});
					}

					// Available production items
					foreach (var buildable in queue.BuildableItems())
						obs.AvailableProduction.Add(buildable.Name);
				}
			}
		}

		RLProto.RlMapInfo SerializeMapInfo()
		{
			return new RLProto.RlMapInfo
			{
				Width = world.Map.MapSize.Width,
				Height = world.Map.MapSize.Height,
				MapName = world.Map.Title ?? "",
			};
		}

		void SerializeSpatialMap(RLProto.GameObservation obs)
		{
			EnsureStaticSpatialMap();

			var map = world.Map;
			var width = map.MapSize.Width;
			var height = map.MapSize.Height;
			var shroud = player.Shroud;

			// Allocate spatial tensor: H × W × channels, row-major channels-last
			var data = (float[])staticSpatialMap.Clone();

			// Pre-compute actor cell positions for unit/building density layers
			var ownBuildingCells = new bool[height * width];
			var ownUnitDensity = new int[height * width];
			var enemyBuildingCells = new bool[height * width];
			var enemyUnitDensity = new int[height * width];

			foreach (var actor in world.Actors)
			{
				if (actor.IsDead || !actor.IsInWorld || actor == world.WorldActor)
					continue;

				if (actor.Owner.NonCombatant)
					continue;

				CPos actorCell;
				try
				{
					actorCell = map.CellContaining(actor.CenterPosition);
				}
				catch (NullReferenceException)
				{
					continue;
				}

				if (actorCell.X < 0 || actorCell.X >= width || actorCell.Y < 0 || actorCell.Y >= height)
					continue;

				var idx = actorCell.Y * width + actorCell.X;

				if (actor.Owner == player)
				{
					if (actor.Info.HasTraitInfo<BuildingInfo>())
						ownBuildingCells[idx] = true;
					else if (actor != player.PlayerActor)
						ownUnitDensity[idx]++;
				}
				else if (shroud.IsVisible(actor.CenterPosition))
				{
					if (actor.Info.HasTraitInfo<BuildingInfo>())
						enemyBuildingCells[idx] = true;
					else
						enemyUnitDensity[idx]++;
				}
			}

			// Fill per-cell data
			foreach (var cell in map.AllCells)
			{
				var x = cell.X;
				var y = cell.Y;
				if (x < 0 || x >= width || y < 0 || y >= height)
					continue;

				var baseIdx = (y * width + x) * SpatialChannelCount;
				var cellIdx = y * width + x;

				// Ch 2: Resource density
				if (resourceLayer != null)
				{
					var resource = resourceLayer.GetResource(cell);
					data[baseIdx + 2] = resource.Density;
				}

				// Ch 4: Fog of war (0=hidden, 0.5=explored, 1=visible)
				if (shroud.IsVisible(cell))
					data[baseIdx + 4] = 1f;
				else if (shroud.IsExplored(cell))
					data[baseIdx + 4] = 0.5f;

				// Ch 5-8: Actor density (pre-computed above)
				data[baseIdx + 5] = ownBuildingCells[cellIdx] ? 1f : 0f;
				data[baseIdx + 6] = ownUnitDensity[cellIdx];
				data[baseIdx + 7] = enemyBuildingCells[cellIdx] ? 1f : 0f;
				data[baseIdx + 8] = enemyUnitDensity[cellIdx];
			}

			// Convert float[] to byte[] for protobuf
			var bytes = new byte[data.Length * sizeof(float)];
			Buffer.BlockCopy(data, 0, bytes, 0, bytes.Length);

			obs.SpatialMap = Google.Protobuf.ByteString.CopyFrom(bytes);
			obs.SpatialChannels = SpatialChannelCount;
		}
	}
}
