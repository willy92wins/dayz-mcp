// camera_path_play (inbox d026): a keyed path for the scripted camera that
// camera_set uses. This class holds the keys, the state and the pose of each
// frame; MCPClientBridge owns the staticcamera, enters it the way a camera_set
// apply does, and calls Advance and Apply from OnTick, before the job runner.
// No Camera.InterpolateTo: every frame's pose is computed here, so the path
// time is the client's frame time accumulated since play.
//
// Bounds mirror tools/dayz_mcp/camera_path.py; the bridge refuses with
// bad_args before the camera moves. The keys arrive as floats, so the
// times are compared as this float holds them.

class MCPCameraPath
{
	static const int KEYS_MIN = 2;
	static const int KEYS_MAX = 64;
	static const float DURATION_MAX_S = 600.0;
	static const int PATH_ID_MAX = 64;
	static const string PATH_ID_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-";
	// LookAt toward the camera's own position has no direction: below this
	// squared distance the frame keeps the orientation it had.
	static const float LOOK_AT_MIN_DISTANCE_SQ = 0.000001;
	// No number of a key is further from 0: the bound entity_wait puts on
	// world positions (tools/dayz_mcp/server.py), which a float holds exactly.
	static const float MAGNITUDE_MAX = 1000000000.0;

	protected ref array<float> m_Times;
	protected ref array<float> m_PosX;
	protected ref array<float> m_PosY;
	protected ref array<float> m_PosZ;
	// cam_orientation (yaw, pitch, roll) unwrapped from key to key the short
	// way round, or the look_at point.
	protected ref array<float> m_AimX;
	protected ref array<float> m_AimY;
	protected ref array<float> m_AimZ;
	protected ref array<float> m_Fov;
	// The last key's cam_orientation or look_at as given: a finished path holds it.
	protected vector m_FinalAim;
	protected bool m_LookAt;
	protected bool m_HasFov;
	protected bool m_Smooth;
	protected string m_PathId;
	// idle (no play yet), playing, finished or aborted.
	protected string m_State;
	protected float m_T;

	void MCPCameraPath()
	{
		m_Times = new array<float>();
		m_PosX = new array<float>();
		m_PosY = new array<float>();
		m_PosZ = new array<float>();
		m_AimX = new array<float>();
		m_AimY = new array<float>();
		m_AimZ = new array<float>();
		m_Fov = new array<float>();
		m_FinalAim = vector.Zero;
		m_LookAt = false;
		m_HasFov = false;
		m_Smooth = true;
		m_PathId = "";
		m_State = "idle";
		m_T = 0.0;
	}

	// A number no further from 0 than MAGNITUDE_MAX. NaN fails every
	// comparison, so it is the one value that needs the self-test; an
	// infinity is past the bound.
	static bool IsBoundedValue(float value)
	{
		if (value != value)
		{
			return false;
		}
		return value >= -MAGNITUDE_MAX && value <= MAGNITUDE_MAX;
	}

	// Up to PATH_ID_MAX characters from A-Z a-z 0-9 _ -; empty is no label.
	static bool IsValidPathId(string pathId)
	{
		int length;
		int index;
		string allowed;
		length = pathId.Length();
		if (length > PATH_ID_MAX)
		{
			return false;
		}

		allowed = PATH_ID_CHARS;
		index = 0;
		while (index < length)
		{
			if (allowed.IndexOf(pathId.Substring(index, 1)) < 0)
			{
				return false;
			}
			index = index + 1;
		}
		return true;
	}

	// Exactly three bounded numbers. An absent JSON array arrives null or empty.
	protected static bool IsBoundedTriple(array<float> values)
	{
		if (!values || values.Count() != 3)
		{
			return false;
		}
		if (!IsBoundedValue(values.Get(0)) || !IsBoundedValue(values.Get(1)))
		{
			return false;
		}
		return IsBoundedValue(values.Get(2));
	}

	protected static bool IsPresent(array<float> values)
	{
		return values && values.Count() > 0;
	}

	// The short way round from previous to angle, in degrees: previous plus
	// their difference wrapped to [-180, 180].
	protected static float UnwrapAngle(float previous, float angle)
	{
		float delta;
		delta = angle - previous;
		delta = delta - 360.0 * Math.Round(delta / 360.0);
		return previous + delta;
	}

	// A play request: "" when every key is valid and the path is loaded,
	// bad_args otherwise. Nothing is kept from a refused request, and the
	// state stays idle until Begin.
	string Load(MCPArgs args)
	{
		int count;
		int index;
		MCPPathKey key;
		bool lookAt;
		bool hasFov;
		float previousT;
		float yaw;
		float pitch;
		float roll;
		array<float> aim;
		if (!args || !args.keys)
		{
			return "bad_args";
		}
		if (args.easing != "linear" && args.easing != "smooth")
		{
			return "bad_args";
		}
		if (!IsValidPathId(args.path_id))
		{
			return "bad_args";
		}

		count = args.keys.Count();
		if (count < KEYS_MIN || count > KEYS_MAX)
		{
			return "bad_args";
		}

		// The first key fixes the kind of aim and whether keys carry a FOV.
		key = args.keys.Get(0);
		if (!key)
		{
			return "bad_args";
		}
		lookAt = IsPresent(key.look_at);
		hasFov = key.fov != 0.0;
		previousT = 0.0;
		index = 0;
		while (index < count)
		{
			key = args.keys.Get(index);
			if (!key)
			{
				return "bad_args";
			}
			if (!IsBoundedValue(key.t))
			{
				return "bad_args";
			}
			if (index == 0 && key.t != 0.0)
			{
				return "bad_args";
			}
			if (index > 0 && key.t <= previousT)
			{
				return "bad_args";
			}
			if (key.t > DURATION_MAX_S)
			{
				return "bad_args";
			}
			if (!IsBoundedTriple(key.cam_pos))
			{
				return "bad_args";
			}
			if (lookAt)
			{
				if (IsPresent(key.cam_orientation) || !IsBoundedTriple(key.look_at))
				{
					return "bad_args";
				}
			}
			else
			{
				if (IsPresent(key.look_at) || !IsBoundedTriple(key.cam_orientation))
				{
					return "bad_args";
				}
			}
			// An absent fov arrives as 0, which is no FOV.
			if (!IsBoundedValue(key.fov) || key.fov < 0.0)
			{
				return "bad_args";
			}
			if (hasFov != (key.fov > 0.0))
			{
				return "bad_args";
			}
			previousT = key.t;
			index = index + 1;
		}

		m_Smooth = args.easing == "smooth";
		m_LookAt = lookAt;
		m_HasFov = hasFov;
		m_PathId = args.path_id;
		m_Times.Clear();
		m_PosX.Clear();
		m_PosY.Clear();
		m_PosZ.Clear();
		m_AimX.Clear();
		m_AimY.Clear();
		m_AimZ.Clear();
		m_Fov.Clear();
		yaw = 0.0;
		pitch = 0.0;
		roll = 0.0;
		index = 0;
		while (index < count)
		{
			key = args.keys.Get(index);
			m_Times.Insert(key.t);
			m_PosX.Insert(key.cam_pos.Get(0));
			m_PosY.Insert(key.cam_pos.Get(1));
			m_PosZ.Insert(key.cam_pos.Get(2));
			if (lookAt)
			{
				aim = key.look_at;
				m_AimX.Insert(aim.Get(0));
				m_AimY.Insert(aim.Get(1));
				m_AimZ.Insert(aim.Get(2));
			}
			else
			{
				aim = key.cam_orientation;
				if (index == 0)
				{
					yaw = aim.Get(0);
					pitch = aim.Get(1);
					roll = aim.Get(2);
				}
				else
				{
					yaw = UnwrapAngle(yaw, aim.Get(0));
					pitch = UnwrapAngle(pitch, aim.Get(1));
					roll = UnwrapAngle(roll, aim.Get(2));
				}
				m_AimX.Insert(yaw);
				m_AimY.Insert(pitch);
				m_AimZ.Insert(roll);
			}
			m_Fov.Insert(key.fov);
			m_FinalAim = Vector(aim.Get(0), aim.Get(1), aim.Get(2));
			index = index + 1;
		}
		return "";
	}

	void Begin()
	{
		m_T = 0.0;
		m_State = "playing";
	}

	bool IsPlaying()
	{
		return m_State == "playing";
	}

	// A stopped or interrupted path keeps its last pose and ends aborted. A
	// finished or idle path is left as it is.
	void Abort()
	{
		if (m_State == "playing")
		{
			m_State = "aborted";
		}
	}

	// True when pathId names this path: the current one or the last one played.
	bool HasPathId(string pathId)
	{
		return m_State != "idle" && m_PathId == pathId;
	}

	float Duration()
	{
		if (m_Times.Count() == 0)
		{
			return 0.0;
		}
		return m_Times.Get(m_Times.Count() - 1);
	}

	vector StartPosition()
	{
		if (m_Times.Count() == 0)
		{
			return vector.Zero;
		}
		return Vector(m_PosX.Get(0), m_PosY.Get(0), m_PosZ.Get(0));
	}

	// One frame: the path time grows by the frame's timeslice and the path
	// finishes on the frame that reaches the last key.
	void Advance(float timeslice)
	{
		float duration;
		if (m_State != "playing")
		{
			return;
		}
		if (timeslice > 0.0)
		{
			m_T = m_T + timeslice;
		}
		duration = Duration();
		if (m_T >= duration)
		{
			m_T = duration;
			m_State = "finished";
		}
	}

	void Fill(MCPCameraPathState report)
	{
		if (!report)
		{
			return;
		}
		report.state = m_State;
		report.t_s = m_T;
		report.duration_s = Duration();
		report.path_id = m_PathId;
	}

	// The pose at the current path time on cam: the position, then the
	// orientation or the look-at point, then the FOV when the keys carry one.
	// At the last key's t it is that key's pose as given.
	void Apply(Camera cam)
	{
		int last;
		int segment;
		float t0;
		float h;
		float s;
		float fov;
		vector pos;
		vector aim;
		if (!cam || m_Times.Count() < KEYS_MIN)
		{
			return;
		}

		last = m_Times.Count() - 1;
		if (m_T >= m_Times.Get(last))
		{
			pos = Vector(m_PosX.Get(last), m_PosY.Get(last), m_PosZ.Get(last));
			aim = m_FinalAim;
			fov = m_Fov.Get(last);
		}
		else
		{
			segment = 0;
			while (segment < last - 1 && m_Times.Get(segment + 1) <= m_T)
			{
				segment = segment + 1;
			}
			t0 = m_Times.Get(segment);
			h = m_Times.Get(segment + 1) - t0;
			s = (m_T - t0) / h;
			pos = Vector(Sample(m_PosX, segment, s, h), Sample(m_PosY, segment, s, h), Sample(m_PosZ, segment, s, h));
			aim = Vector(Sample(m_AimX, segment, s, h), Sample(m_AimY, segment, s, h), Sample(m_AimZ, segment, s, h));
			fov = m_Fov.Get(segment) + (m_Fov.Get(segment + 1) - m_Fov.Get(segment)) * s;
		}

		cam.SetPosition(pos);
		if (m_LookAt)
		{
			if (vector.DistanceSq(pos, aim) > LOOK_AT_MIN_DISTANCE_SQ)
			{
				cam.LookAt(aim);
			}
		}
		else
		{
			cam.SetOrientation(aim);
		}
		if (m_HasFov)
		{
			cam.SetFOV(fov);
		}
	}

	// One component at fraction s of segment (h seconds long). linear: the
	// straight line between the two keys. smooth: Catmull-Rom by time, the
	// cubic Hermite through both keys with the tangents of ScaledTangent.
	protected float Sample(array<float> values, int segment, float s, float h)
	{
		float p0;
		float p1;
		float m0;
		float m1;
		float s2;
		float s3;
		p0 = values.Get(segment);
		p1 = values.Get(segment + 1);
		if (!m_Smooth)
		{
			return p0 + (p1 - p0) * s;
		}

		m0 = ScaledTangent(values, segment, h);
		m1 = ScaledTangent(values, segment + 1, h);
		s2 = s * s;
		s3 = s2 * s;
		return (2.0 * s3 - 3.0 * s2 + 1.0) * p0 + (s3 - 2.0 * s2 + s) * m0 + (3.0 * s2 - 2.0 * s3) * p1 + (s3 - s2) * m1;
	}

	// h times the rate per second at key index, the tangent the Hermite
	// takes: the change from the previous to the next key times h over their
	// time apart, the end segment's at either end. Those two keys are at
	// least as far apart as the segment's own, so the ratio is at most 1 and
	// a short segment cannot overflow the rate.
	protected float ScaledTangent(array<float> values, int index, float h)
	{
		int before;
		int after;
		before = index - 1;
		after = index + 1;
		if (before < 0)
		{
			before = 0;
		}
		if (after > values.Count() - 1)
		{
			after = values.Count() - 1;
		}
		return (values.Get(after) - values.Get(before)) * (h / (m_Times.Get(after) - m_Times.Get(before)));
	}
};
