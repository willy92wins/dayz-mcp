class MCPCarDrive
{
	static bool s_Active;
	static CarScript s_Car;
	static float s_Throttle;
	static float s_Steer;
	static float s_Brake;
	static float s_Handbrake;
	static float s_DeadlineS;

	static void Set(CarScript car, float throttle, float steer, float brake, float handbrake, float deadlineS)
	{
		s_Car = car;
		s_Throttle = throttle;
		s_Steer = steer;
		s_Brake = brake;
		s_Handbrake = handbrake;
		s_DeadlineS = deadlineS;
		s_Active = true;
	}

	static void Clear()
	{
		s_Active = false;
		s_Car = null;
	}
}

class MCPVehicleTraceSample
{
	int sequence;
	float monotonic_s;
	float sample_dt_s;
	bool forced;
	float position_x;
	float position_y;
	float position_z;
	float velocity_x;
	float velocity_y;
	float velocity_z;
	float direction_x;
	float direction_y;
	float direction_z;
	float yaw_deg;
	float pitch_deg;
	float roll_deg;
	bool control_active;
	float throttle_requested;
	float steer_requested;
	float brake_requested;
	float handbrake_requested;
	float throttle_applied;
	float steer_applied;
	float brake_applied;
	float handbrake_applied;
	bool wheel_contact_0;
	bool wheel_contact_1;
	bool wheel_contact_2;
	bool wheel_contact_3;
	float wheel_angular_velocity_0;
	float wheel_angular_velocity_1;
	float wheel_angular_velocity_2;
	float wheel_angular_velocity_3;
	int wheel_count;
	int wheels_present;
	bool engine_on;
	int gear;
	bool is_owner;
	bool is_authority_owner;
	int net_id_low;
	int net_id_high;
	bool wheel_loss_event;
	int wheel_loss_count;
	int contact_count;
	int body_contact_count;
	string body_contact_zone;
	float body_contact_impulse;
	float body_contact_local_x;
	float body_contact_local_y;
	float body_contact_local_z;
	float body_contact_normal_x;
	float body_contact_normal_y;
	float body_contact_normal_z;
	float body_contact_penetration_depth;
};

class MCPVehicleTraceRead
{
	string schema;
	string mode;
	string trace_id;
	bool active;
	bool complete;
	bool overflow;
	string stop_reason;
	int sample_hz;
	int capacity;
	int count;
	float start_monotonic_s;
	string owner_identity;
	string car_type;
	int net_id_low;
	int net_id_high;
	int cursor;
	int next_cursor;
	bool eof;
	ref array<ref MCPVehicleTraceSample> samples;

	void MCPVehicleTraceRead()
	{
		samples = new array<ref MCPVehicleTraceSample>();
	}
};

class MCPVehicleTrace
{
	static const string SCHEMA = "dayz-mcp-vehicle-trace-v1";

	static bool s_Active;
	static bool s_Complete;
	static bool s_Overflow;
	static string s_StopReason;
	static string s_LastError;
	static string s_TraceId;
	static int s_SampleHz;
	static int s_Capacity;
	static int s_Count;
	static float s_StartMonotonicS;
	static float s_LastSampleS;
	static float s_AccumS;
	static CarScript s_Car;
	static string s_OwnerIdentity;
	static string s_CarType;
	static int s_NetIdLow;
	static int s_NetIdHigh;
	static int s_PreviousWheelsPresent;
	static int s_WheelLossCount;
	static int s_PendingContactCount;
	static int s_PendingBodyContactCount;
	static string s_PendingBodyContactZone;
	static float s_PendingBodyContactImpulse;
	static vector s_PendingBodyContactLocal;
	static vector s_PendingBodyContactNormal;
	static float s_PendingBodyContactPenetration;
	static ref array<ref MCPVehicleTraceSample> s_Samples;

	static bool Start(CarScript car, string traceId, int sampleHz, int maxSamples)
	{
		s_LastError = "";
		if (!car || !car.IsOwner())
		{
			s_LastError = "not_owner";
			return false;
		}
		if (s_TraceId != "")
		{
			s_LastError = "trace_exists";
			return false;
		}
		if (sampleHz < 20 || sampleHz > 60 || maxSamples < 2 || maxSamples > 8192)
		{
			s_LastError = "bad_args";
			return false;
		}

		s_Samples = new array<ref MCPVehicleTraceSample>();
		s_Samples.Resize(maxSamples);
		int index = 0;
		while (index < maxSamples)
		{
			s_Samples.Set(index, new MCPVehicleTraceSample());
			index = index + 1;
		}

		s_TraceId = traceId;
		s_SampleHz = sampleHz;
		s_Capacity = maxSamples;
		s_Count = 0;
		s_StartMonotonicS = GetGame().GetTickTime();
		s_LastSampleS = 0.0;
		s_AccumS = 0.0;
		s_Car = car;
		s_CarType = car.GetType();
		s_Complete = false;
		s_Overflow = false;
		s_StopReason = "";
		s_PreviousWheelsPresent = car.WheelCountPresent();
		s_WheelLossCount = 0;
		ResetPendingContact();

		PlayerIdentity ownerIdentity = car.GetOwnerIdentity();
		if (ownerIdentity)
		{
			s_OwnerIdentity = ownerIdentity.GetPlainId();
		}
		else
		{
			s_OwnerIdentity = "";
		}
		car.GetNetworkID(s_NetIdLow, s_NetIdHigh);
		s_Active = true;
		return true;
	}

	static void Capture(CarScript car, float dt)
	{
		if (!s_Active || car != s_Car)
		{
			return;
		}
		if (!car.IsOwner())
		{
			Fail("owner_changed");
			return;
		}

		s_AccumS = s_AccumS + dt;
		float intervalS = 1.0 / s_SampleHz;
		if (s_Count > 0 && s_AccumS < intervalS)
		{
			return;
		}
		float nowS = GetGame().GetTickTime();
		if (s_Count > 0 && nowS < s_LastSampleS)
		{
			Fail("clock_not_monotonic");
			return;
		}
		if (s_Count > 0 && nowS == s_LastSampleS)
		{
			return;
		}
		if (s_Count > 0)
		{
			s_AccumS = s_AccumS - intervalS;
		}
		else
		{
			s_AccumS = 0.0;
		}
		CaptureNow(car, false);
	}

	static void CaptureContact(CarScript car, string zoneName, vector localPos, Contact data)
	{
		if (!s_Active || car != s_Car || !data)
		{
			return;
		}

		s_PendingContactCount = s_PendingContactCount + 1;
		bool wheelZone = zoneName.IndexOf("wheel") >= 0;
		if (!wheelZone)
		{
			wheelZone = zoneName.IndexOf("Wheel") >= 0;
		}
		if (!wheelZone)
		{
			wheelZone = zoneName.IndexOf("WHEEL") >= 0;
		}
		if (wheelZone)
		{
			return;
		}

		s_PendingBodyContactCount = s_PendingBodyContactCount + 1;
		if (s_PendingBodyContactCount == 1 || data.Impulse > s_PendingBodyContactImpulse)
		{
			s_PendingBodyContactZone = zoneName;
			s_PendingBodyContactImpulse = data.Impulse;
			s_PendingBodyContactLocal = localPos;
			s_PendingBodyContactNormal = data.Normal;
			s_PendingBodyContactPenetration = data.PenetrationDepth;
		}
	}

	static bool Stop(string traceId)
	{
		s_LastError = "";
		if (!Matches(traceId))
		{
			s_LastError = "trace_not_found";
			return false;
		}
		if (!s_Active)
		{
			return true;
		}
		if (s_PendingContactCount > 0)
		{
			float nowS = GetGame().GetTickTime();
			if (s_Count > 0 && nowS <= s_LastSampleS)
			{
				Fail("contact_flush_clock");
				return false;
			}
			CaptureNow(s_Car, true);
			if (!s_Active)
			{
				return false;
			}
		}
		s_Active = false;
		s_Complete = true;
		s_StopReason = "requested";
		return true;
	}

	static bool Clear(string traceId)
	{
		s_LastError = "";
		if (!Matches(traceId))
		{
			s_LastError = "trace_not_found";
			return false;
		}
		if (s_Active)
		{
			s_LastError = "trace_active";
			return false;
		}
		ClearState();
		return true;
	}

	static void Abort(string reason)
	{
		if (s_TraceId == "")
		{
			return;
		}
		ClearState();
		s_LastError = reason;
	}

	static void Fail(string reason)
	{
		if (s_TraceId == "")
		{
			return;
		}
		s_Active = false;
		s_Complete = false;
		s_StopReason = reason;
		s_LastError = reason;
	}

	static bool Matches(string traceId)
	{
		return s_TraceId != "" && s_TraceId == traceId;
	}

	static bool IsActive()
	{
		return s_Active;
	}

	static CarScript GetCar()
	{
		return s_Car;
	}

	static string GetLastError()
	{
		return s_LastError;
	}

	static MCPVehicleTraceRead View(string mode, string traceId, int cursor, int limit)
	{
		s_LastError = "";
		if (!Matches(traceId))
		{
			s_LastError = "trace_not_found";
			return null;
		}
		if (cursor < 0 || cursor > s_Count || limit < 1 || limit > 64)
		{
			s_LastError = "bad_args";
			return null;
		}

		MCPVehicleTraceRead view = new MCPVehicleTraceRead();
		view.schema = SCHEMA;
		view.mode = mode;
		view.trace_id = s_TraceId;
		view.active = s_Active;
		view.complete = s_Complete;
		view.overflow = s_Overflow;
		view.stop_reason = s_StopReason;
		view.sample_hz = s_SampleHz;
		view.capacity = s_Capacity;
		view.count = s_Count;
		view.start_monotonic_s = s_StartMonotonicS;
		view.owner_identity = s_OwnerIdentity;
		view.car_type = s_CarType;
		view.net_id_low = s_NetIdLow;
		view.net_id_high = s_NetIdHigh;
		view.cursor = cursor;

		int end = cursor;
		if (mode == "read")
		{
			end = cursor + limit;
			if (end > s_Count)
			{
				end = s_Count;
			}
			int index = cursor;
			while (index < end)
			{
				view.samples.Insert(s_Samples.Get(index));
				index = index + 1;
			}
		}
		view.next_cursor = end;
		view.eof = !s_Active && end == s_Count;
		return view;
	}

	protected static void CaptureNow(CarScript car, bool forced)
	{
		if (!s_Active || !car || car != s_Car)
		{
			return;
		}
		if (s_Count >= s_Capacity)
		{
			s_Overflow = true;
			Fail("overflow");
			return;
		}

		float nowS = GetGame().GetTickTime();
		if (s_Count > 0 && nowS <= s_LastSampleS)
		{
			Fail("clock_not_monotonic");
			return;
		}

		MCPVehicleTraceSample sample = s_Samples.Get(s_Count);
		vector position = car.GetPosition();
		vector velocity = GetVelocity(car);
		vector direction = car.GetDirection();
		vector orientation = car.GetOrientation();
		int lowBits = 0;
		int highBits = 0;
		car.GetNetworkID(lowBits, highBits);

		sample.sequence = s_Count;
		sample.monotonic_s = nowS;
		if (s_Count == 0)
		{
			sample.sample_dt_s = 0.0;
		}
		else
		{
			sample.sample_dt_s = nowS - s_LastSampleS;
		}
		sample.forced = forced;
		sample.position_x = position[0];
		sample.position_y = position[1];
		sample.position_z = position[2];
		sample.velocity_x = velocity[0];
		sample.velocity_y = velocity[1];
		sample.velocity_z = velocity[2];
		sample.direction_x = direction[0];
		sample.direction_y = direction[1];
		sample.direction_z = direction[2];
		sample.yaw_deg = orientation[0];
		sample.pitch_deg = orientation[1];
		sample.roll_deg = orientation[2];

		sample.control_active = MCPCarDrive.s_Active && MCPCarDrive.s_Car == car;
		if (sample.control_active)
		{
			sample.throttle_requested = MCPCarDrive.s_Throttle;
			sample.steer_requested = MCPCarDrive.s_Steer;
			sample.brake_requested = MCPCarDrive.s_Brake;
			sample.handbrake_requested = MCPCarDrive.s_Handbrake;
		}
		else
		{
			sample.throttle_requested = 0.0;
			sample.steer_requested = 0.0;
			sample.brake_requested = 0.0;
			sample.handbrake_requested = 0.0;
		}
		sample.throttle_applied = car.GetThrottle();
		sample.steer_applied = car.GetSteering();
		sample.brake_applied = car.GetBrake();
		sample.handbrake_applied = car.GetHandbrake();

		int wheelCount = car.WheelCount();
		sample.wheel_count = wheelCount;
		sample.wheels_present = car.WheelCountPresent();
		sample.wheel_contact_0 = false;
		sample.wheel_contact_1 = false;
		sample.wheel_contact_2 = false;
		sample.wheel_contact_3 = false;
		sample.wheel_angular_velocity_0 = 0.0;
		sample.wheel_angular_velocity_1 = 0.0;
		sample.wheel_angular_velocity_2 = 0.0;
		sample.wheel_angular_velocity_3 = 0.0;
		if (wheelCount > 0)
		{
			sample.wheel_contact_0 = car.WheelHasContact(0);
			sample.wheel_angular_velocity_0 = car.WheelGetAngularVelocity(0);
		}
		if (wheelCount > 1)
		{
			sample.wheel_contact_1 = car.WheelHasContact(1);
			sample.wheel_angular_velocity_1 = car.WheelGetAngularVelocity(1);
		}
		if (wheelCount > 2)
		{
			sample.wheel_contact_2 = car.WheelHasContact(2);
			sample.wheel_angular_velocity_2 = car.WheelGetAngularVelocity(2);
		}
		if (wheelCount > 3)
		{
			sample.wheel_contact_3 = car.WheelHasContact(3);
			sample.wheel_angular_velocity_3 = car.WheelGetAngularVelocity(3);
		}

		int wheelDrop = s_PreviousWheelsPresent - sample.wheels_present;
		if (wheelDrop > 0)
		{
			s_WheelLossCount = s_WheelLossCount + wheelDrop;
			sample.wheel_loss_event = true;
		}
		else
		{
			sample.wheel_loss_event = false;
		}
		sample.wheel_loss_count = s_WheelLossCount;
		s_PreviousWheelsPresent = sample.wheels_present;

		sample.engine_on = car.EngineIsOn();
		sample.gear = car.GetGear();
		sample.is_owner = car.IsOwner();
		sample.is_authority_owner = car.IsAuthorityOwner();
		sample.net_id_low = lowBits;
		sample.net_id_high = highBits;
		sample.contact_count = s_PendingContactCount;
		sample.body_contact_count = s_PendingBodyContactCount;
		sample.body_contact_zone = s_PendingBodyContactZone;
		sample.body_contact_impulse = s_PendingBodyContactImpulse;
		sample.body_contact_local_x = s_PendingBodyContactLocal[0];
		sample.body_contact_local_y = s_PendingBodyContactLocal[1];
		sample.body_contact_local_z = s_PendingBodyContactLocal[2];
		sample.body_contact_normal_x = s_PendingBodyContactNormal[0];
		sample.body_contact_normal_y = s_PendingBodyContactNormal[1];
		sample.body_contact_normal_z = s_PendingBodyContactNormal[2];
		sample.body_contact_penetration_depth = s_PendingBodyContactPenetration;

		s_LastSampleS = nowS;
		s_Count = s_Count + 1;
		ResetPendingContact();
	}

	protected static void ResetPendingContact()
	{
		s_PendingContactCount = 0;
		s_PendingBodyContactCount = 0;
		s_PendingBodyContactZone = "";
		s_PendingBodyContactImpulse = 0.0;
		s_PendingBodyContactLocal = "0 0 0";
		s_PendingBodyContactNormal = "0 0 0";
		s_PendingBodyContactPenetration = 0.0;
	}

	protected static void ClearState()
	{
		s_Active = false;
		s_Complete = false;
		s_Overflow = false;
		s_StopReason = "";
		s_TraceId = "";
		s_SampleHz = 0;
		s_Capacity = 0;
		s_Count = 0;
		s_StartMonotonicS = 0.0;
		s_LastSampleS = 0.0;
		s_AccumS = 0.0;
		s_Car = null;
		s_OwnerIdentity = "";
		s_CarType = "";
		s_NetIdLow = 0;
		s_NetIdHigh = 0;
		s_PreviousWheelsPresent = 0;
		s_WheelLossCount = 0;
		ResetPendingContact();
		if (s_Samples)
		{
			s_Samples.Clear();
			s_Samples = null;
		}
	}
}

modded class CarScript
{
	override void OnInput(float dt)
	{
		super.OnInput(dt);

		bool applyControl = MCPCarDrive.s_Active && MCPCarDrive.s_Car == this;
		if (applyControl && GetGame().GetTickTime() > MCPCarDrive.s_DeadlineS)
		{
			MCPCarDrive.Clear();
			applyControl = false;
		}

		if (applyControl)
		{
			float rpm = EngineGetRPM();
			float spd = GetSpeedometerAbsolute();
			bool engineReady = true;

			// Engine management (espeja autopiloto :1311-1325)
			if (rpm < EngineGetRPMIdle())
			{
				if (rpm < 1.0 && EngineIsOn())
				{
					EngineStop();
				}
				else if (!EngineIsOn())
				{
					EngineStart();
				}
				engineReady = false;
			}

			if (engineReady)
			{
				float throttle = MCPCarDrive.s_Throttle;

				// Marcha manual (espeja autopiloto :1353-1375)
				bool isManual = GearboxGetType() == CarGearboxType.MANUAL;
				if (isManual)
				{
					if (rpm > EngineGetRPMRedline() * 0.8)
					{
						if (throttle > 0.1)
						{
							ShiftUp();
						}
					}
					else if (GetGear() < CarGear.FIRST || (throttle > 0.0 && spd < 5.0))
					{
						ShiftTo(CarGear.FIRST);
					}
				}

				SetThrottle(throttle);
				SetSteering(MCPCarDrive.s_Steer);
				SetBrake(MCPCarDrive.s_Brake);
				SetHandbrake(MCPCarDrive.s_Handbrake);
				SetBrakesActivateWithoutDriver(false);
			}
		}

		MCPVehicleTrace.Capture(this, dt);
	}

	override void OnContact(string zoneName, vector localPos, IEntity other, Contact data)
	{
		super.OnContact(zoneName, localPos, other, data);
		MCPVehicleTrace.CaptureContact(this, zoneName, localPos, data);
	}
}
