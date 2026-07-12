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
using System.Threading;
using System.Threading.Channels;
using System.Threading.Tasks;
using Grpc.Core;
using RLProto = OpenRA.Mods.Common.RL;

namespace OpenRA.Mods.Common.Traits
{
	/// <summary>
	/// gRPC service implementation for the RL Bridge.
	/// Routes RPCs to the correct ExternalBotBridge session via session_id.
	/// In single-session (legacy) mode, session_id can be empty.
	/// </summary>
	public sealed class RLBridgeService : RLProto.RLBridge.RLBridgeBase
	{
		/// <summary>
		/// Wait for a bridge to become available and activated.
		/// In multi-session mode with session_id, looks up immediately.
		/// In single-session mode, polls until the bridge activates (up to 60s).
		/// </summary>
		static async Task<ExternalBotBridge> WaitForBridge(string sessionId, CancellationToken ct)
		{
			// In multi-session mode with a specific session_id, look up directly
			if (!string.IsNullOrEmpty(sessionId))
			{
				// Session should already exist (created by CreateSession RPC)
				// CreateSession now blocks until ready, but keep a generous timeout
				for (var i = 0; i < 3000; i++)
				{
					var b = ExternalBotBridge.LookupSession(sessionId);
					if (b != null && b.IsEnabled)
						return b;

					await Task.Delay(100, ct);
				}

				return null;
			}

			// Legacy single-session mode: wait for any bridge to activate
			for (var i = 0; i < 600; i++)
			{
				var bridge = ExternalBotBridge.LookupSession("");
				if (bridge != null && bridge.IsEnabled)
					return bridge;

				await Task.Delay(100, ct);
			}

			return null;
		}

		static async Task<IObservationSession> WaitForObservationSession(string sessionId, CancellationToken ct)
		{
			var attempts = string.IsNullOrEmpty(sessionId) ? 600 : 3000;
			for (var i = 0; i < attempts; i++)
			{
				var session = ObservationSessionRegistry.Lookup(sessionId);
				if (session != null && session.IsEnabled)
					return session;

				await Task.Delay(100, ct);
			}

			return null;
		}

		/// <summary>
		/// Bidirectional streaming: game sends observations, agent sends actions.
		/// </summary>
		public override async Task GameSession(
			IAsyncStreamReader<RLProto.AgentAction> requestStream,
			IServerStreamWriter<RLProto.GameObservation> responseStream,
			ServerCallContext context)
		{
			// For streaming, we don't have session_id upfront — use legacy lookup
			var bridge = await WaitForBridge("", context.CancellationToken);

			if (bridge == null)
			{
				Log.Write("rl-bridge", "GameSession rejected: bridge not activated within 60s timeout");
				return;
			}

			bridge.OnAgentConnected();
			Log.Write("rl-bridge", $"GameSession started for session {bridge.SessionId}");

			var ct = context.CancellationToken;

			try
			{
				// Observation sender: game → agent (runs independently)
				var obsTask = Task.Run(async () =>
				{
					try
					{
						while (!ct.IsCancellationRequested)
						{
							var obs = await bridge.ObservationReader.ReadAsync(ct);
							await responseStream.WriteAsync(obs, ct);
							if (obs.Done)
							{
								Log.Write("rl-bridge", $"Game over: {obs.Result}");
								break;
							}
						}
					}
					catch (OperationCanceledException) { }
					catch (ChannelClosedException) { }
				}, ct);

				// Action receiver: agent → game (runs independently)
				var actionTask = Task.Run(async () =>
				{
					try
					{
						while (await requestStream.MoveNext(ct))
							await bridge.ActionWriter.WriteAsync(requestStream.Current, ct);
					}
					catch (OperationCanceledException) { }
					catch (ChannelClosedException) { }
				}, ct);

				// Exit when either loop ends (game over, disconnect, or cancel)
				await Task.WhenAny(obsTask, actionTask);
			}
			finally
			{
				bridge.OnAgentDisconnected();
				Log.Write("rl-bridge", "GameSession ended");
			}
		}

		/// <summary>
		/// Unary RPC: advance N ticks with optional commands, return observation.
		/// Routes to the correct session via request.SessionId.
		/// </summary>
		public override async Task<RLProto.GameObservation> FastAdvance(
			RLProto.FastAdvanceRequest request,
			ServerCallContext context)
		{
			try
			{
				var bridge = await WaitForBridge(request.SessionId, context.CancellationToken);

				if (bridge == null)
					throw new RpcException(new Status(StatusCode.Unavailable,
						$"Bridge not activated within 300s (session_id={request.SessionId})"));

				return await bridge.RequestFastAdvance(
					request.Ticks, request.Commands, context.CancellationToken,
					request.CheckEventsEvery,
					request.EnabledInterrupts.Count > 0 ? request.EnabledInterrupts : null);
			}
			catch (RpcException)
			{
				throw;
			}
			catch (Exception e)
			{
				Log.Write("rl-bridge", $"FastAdvance error for session {request.SessionId}: {e}");
				throw;
			}
		}

		/// <summary>
		/// Unary RPC: query current game state on demand.
		/// </summary>
		public override Task<RLProto.GameState> GetState(
			RLProto.StateRequest request,
			ServerCallContext context)
		{
			if (RLSessionManager.TryGetSessionFailure(request.SessionId, out var errorCode, out var errorMessage))
			{
				return Task.FromResult(new RLProto.GameState
				{
					EpisodeId = request.SessionId,
					Phase = "error",
					ErrorCode = errorCode,
					ErrorMessage = errorMessage,
				});
			}

			var observationSession = ObservationSessionRegistry.Lookup(request.SessionId);
			if (observationSession != null)
				return Task.FromResult(observationSession.GetCurrentState());

			if (RLSessionManager.TryGetObservationlessState(request.SessionId, out var observationlessState))
				return Task.FromResult(observationlessState);

			var bridge = ExternalBotBridge.LookupSession(request.SessionId);
			if (bridge == null)
			{
				return Task.FromResult(new RLProto.GameState
				{
					Phase = "no_bridge"
				});
			}

			return Task.FromResult(bridge.GetCurrentState());
		}

		/// <summary>
		/// Read a continuous, read-only observation stream from agent-normal.
		/// An observer disconnect never stops its game session.
		/// </summary>
		public override async Task StreamObservations(
			RLProto.StateRequest request,
			IServerStreamWriter<RLProto.GameObservation> responseStream,
			ServerCallContext context)
		{
			var session = await WaitForObservationSession(request.SessionId, context.CancellationToken);
			if (session == null)
				throw new RpcException(new Status(StatusCode.Unavailable,
					$"Observation session not activated (session_id={request.SessionId})"));

			session.OnObserverConnected();
			try
			{
				while (!context.CancellationToken.IsCancellationRequested)
				{
					var observation = await session.ObservationReader.ReadAsync(context.CancellationToken);
					await responseStream.WriteAsync(observation, context.CancellationToken);
					if (observation.Done)
						return;
				}
			}
			catch (OperationCanceledException) { }
			catch (ChannelClosedException) { }
			finally
			{
				session.OnObserverDisconnected();
			}
		}

		/// <summary>
		/// Create a new game session. Only available in multi-session mode.
		/// </summary>
		public override Task<RLProto.CreateSessionResponse> CreateSession(
			RLProto.CreateSessionRequest request,
			ServerCallContext context)
		{
			if (!RLSessionManager.IsMultiSessionMode)
				throw new RpcException(new Status(StatusCode.Unimplemented,
					"CreateSession is only available in multi-session mode"));

			var sessionId = RLSessionManager.CreateSession(request.MapName, request.Bots, request.Seed,
				request.PlayerFaction, request.EnemyFaction, request.PlayerSpawn, request.EnemySpawn);

			return Task.FromResult(new RLProto.CreateSessionResponse
			{
				SessionId = sessionId
			});
		}

		/// <summary>
		/// Destroy a game session. Only available in multi-session mode.
		/// </summary>
		public override Task<RLProto.DestroySessionResponse> DestroySession(
			RLProto.DestroySessionRequest request,
			ServerCallContext context)
		{
			if (!RLSessionManager.IsMultiSessionMode)
				throw new RpcException(new Status(StatusCode.Unimplemented,
					"DestroySession is only available in multi-session mode"));

			// Destroy is best-effort — never fail the RPC
			try { RLSessionManager.DestroySession(request.SessionId); }
			catch (Exception e) { Log.Write("rl-bridge", $"DestroySession error: {e}"); }

			return Task.FromResult(new RLProto.DestroySessionResponse());
		}
	}
}
