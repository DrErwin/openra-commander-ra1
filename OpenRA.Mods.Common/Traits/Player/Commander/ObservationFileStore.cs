#region Copyright & License Information
/*
 * Copyright (c) The OpenRA Developers and Contributors
 * This file is part of OpenRA, which is free software. It is made
 * available under the terms of the GNU General Public License
 * as published by the Free Software Foundation, either version 3 of
 * the License, or (at your option) any later version. For more
 * information, see COPYING.
 */
#endregion

using System;
using System.IO;
using System.Text;
using System.Text.Json;
using System.Threading;
using Google.Protobuf;
using RLProto = OpenRA.Mods.Common.RL;

namespace OpenRA.Mods.Common.Traits.Commander
{
	/// <summary>
	/// A1.5 latest-snapshot publisher. Each file is replaced atomically and the
	/// sequence/timestamp in the JSON envelope lets readers reject mixed files.
	/// </summary>
	sealed class ObservationFileStore
	{
		const string SchemaVersion = "1";
		readonly string sessionDirectory;
		readonly object pendingLock = new();
		Thread writerThread;
		RLProto.GameObservation pendingObservation;
		bool stopping;

		public ObservationFileStore(string sessionId)
		{
			if (string.IsNullOrWhiteSpace(sessionId) || sessionId != Path.GetFileName(sessionId))
				throw new ArgumentException("Invalid observation session id.", nameof(sessionId));

			var configuredRoot = Environment.GetEnvironmentVariable("RL_OBSERVATION_DIR");
			var root = string.IsNullOrWhiteSpace(configuredRoot)
				? Path.Combine(Platform.SupportDir, "RLBridge", "runtime")
				: configuredRoot;
			root = Path.GetFullPath(root);
			sessionDirectory = Path.Combine(root, sessionId);
		}

		public string SessionDirectory => sessionDirectory;

		public void Publish(RLProto.GameObservation observation)
		{
			if (observation == null)
				return;

			// The game thread only replaces a single in-memory reference. The
			// writer thread performs all directory, serialization and disk I/O.
			lock (pendingLock)
			{
				if (stopping)
					return;

				if (writerThread == null)
				{
					writerThread = new Thread(WriteLoop)
					{
						IsBackground = true,
						Name = $"RL-Observation-File-{observation.EpisodeId}"
					};
					writerThread.Start();
				}

				pendingObservation = observation;
				Monitor.Pulse(pendingLock);
			}
		}

		void WriteLoop()
		{
			while (true)
			{
				RLProto.GameObservation observation;
				lock (pendingLock)
				{
					while (pendingObservation == null && !stopping)
						Monitor.Wait(pendingLock);

					if (pendingObservation == null && stopping)
						return;

					observation = pendingObservation;
					pendingObservation = null;
				}

				try
				{
					WriteSnapshot(observation);
				}
				catch (Exception e)
				{
					Log.Write("rl-bridge", $"event=observation_file_publish_error session={observation.EpisodeId} error={e.Message}");
				}
			}
		}

		void WriteSnapshot(RLProto.GameObservation observation)
		{
			Directory.CreateDirectory(sessionDirectory);
			var protobufPath = Path.Combine(sessionDirectory, "latest-observation.pb");
			var jsonPath = Path.Combine(sessionDirectory, "latest-observation.json");

			WriteAtomically(protobufPath, observation.ToByteArray());

			var observationJson = JsonFormatter.Default.Format(observation);
			var envelope = "{" +
				$"\"schema_version\":{JsonSerializer.Serialize(SchemaVersion)}," +
				$"\"session_id\":{JsonSerializer.Serialize(observation.EpisodeId)}," +
				$"\"observation_sequence\":{observation.ObservationSequence}," +
				$"\"observed_at_unix_ms\":{observation.ObservedAtUnixMs}," +
				$"\"observation\":{observationJson}" +
				"}";
			WriteAtomically(jsonPath, Encoding.UTF8.GetBytes(envelope));
		}

		public void Delete()
		{
			lock (pendingLock)
			{
				stopping = true;
				Monitor.PulseAll(pendingLock);
			}

			if (writerThread != null && writerThread != Thread.CurrentThread)
				writerThread.Join(TimeSpan.FromSeconds(10));

			if (Directory.Exists(sessionDirectory))
				Directory.Delete(sessionDirectory, recursive: true);
		}

		static void WriteAtomically(string destination, byte[] bytes)
		{
			var temporary = destination + $".{Guid.NewGuid():N}.tmp";
			try
			{
				using (var stream = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None))
				{
					stream.Write(bytes, 0, bytes.Length);
					stream.Flush(flushToDisk: true);
				}

				File.Move(temporary, destination, overwrite: true);
			}
			finally
			{
				if (File.Exists(temporary))
					File.Delete(temporary);
			}
		}
	}
}
