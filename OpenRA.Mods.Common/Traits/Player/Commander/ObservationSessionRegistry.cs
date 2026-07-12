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
using System.Collections.Concurrent;
using System.Threading.Channels;
using RLProto = OpenRA.Mods.Common.RL;

namespace OpenRA.Mods.Common.Traits.Commander
{
	/// <summary>
	/// Read-only observation endpoint for one game session.  The game thread is
	/// the only writer; transport code reads from <see cref="ObservationReader"/>.
	/// </summary>
	public interface IObservationSession
	{
		string SessionId { get; }
		bool IsEnabled { get; }
		int ObserverCount { get; }
		DateTime LastObserverActivityUtc { get; }
		bool IsGameOver { get; }
		ChannelReader<RLProto.GameObservation> ObservationReader { get; }
		RLProto.GameState GetCurrentState();
		void OnObserverConnected();
		void OnObserverDisconnected();
		void Deactivate();
	}

	/// <summary>
	/// Process-local registry. Empty identifiers are accepted only when exactly
	/// one endpoint exists, preventing accidental cross-session reads.
	/// </summary>
	public static class ObservationSessionRegistry
	{
		static readonly ConcurrentDictionary<string, IObservationSession> Sessions = new();
		static readonly object RegistrationLock = new();

		public static volatile bool MultiSessionMode;

		[ThreadStatic]
		public static string NextSessionId;

		public static bool TryRegister(IObservationSession session)
		{
			if (session == null || string.IsNullOrEmpty(session.SessionId))
				return false;

			lock (RegistrationLock)
			{
				// The legacy single-session host has one world and therefore exactly
				// one observation endpoint. Multi-session mode keys endpoints by id.
				if (!MultiSessionMode && !Sessions.IsEmpty)
					return false;

				return Sessions.TryAdd(session.SessionId, session);
			}
		}

		public static void Remove(string sessionId)
		{
			if (!string.IsNullOrEmpty(sessionId))
				Sessions.TryRemove(sessionId, out _);
		}

		public static IObservationSession Lookup(string sessionId)
		{
			if (!string.IsNullOrEmpty(sessionId))
				return Sessions.TryGetValue(sessionId, out var session) ? session : null;

			if (Sessions.Count != 1)
				return null;

			foreach (var session in Sessions.Values)
				return session;

			return null;
		}
	}
}
