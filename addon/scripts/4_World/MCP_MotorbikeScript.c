// fb-20261127-181405-b95f: applies vehicle_control to the motorbike the local driver owns, as CarScript.OnInput does for a car.
modded class MotorbikeScript
{
	override void OnInput(float dt)
	{
		super.OnInput(dt);

		bool applyControl = MCPCarDrive.s_Active && MCPCarDrive.s_Bike == this;
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

			// Engine management. Only a throttle request starts or restarts
			// the engine; a brake-only command leaves the engine as it is.
			if (rpm < EngineGetRPMIdle())
			{
				if (MCPCarDrive.s_Throttle > 0.0)
				{
					if (rpm < 1.0 && EngineIsOn())
					{
						EngineStop();
					}
					else if (!EngineIsOn())
					{
						EngineStart();
					}
				}
				// Below idle a running engine is ready on every gearbox. SetThrottle
				// has to run or the native input step keeps the driver's throttle at 0.
				if (!EngineIsOn() || rpm < 1.0)
				{
					engineReady = false;
				}
			}

			// Brakes do not wait for the engine. Rear brake, then front brake.
			SetBrake(MCPCarDrive.s_Brake);
			SetHandbrake(MCPCarDrive.s_Handbrake);

			if (engineReady)
			{
				float throttle = MCPCarDrive.s_Throttle;

				bool isManual = GearboxGetType() == CarGearboxType.MANUAL;
				if (isManual)
				{
					// First gear is one above this bike's own neutral.
					int firstGear = GetNeutralGear() + 1;
					if (rpm > EngineGetRPMRedline() * 0.8)
					{
						if (throttle > 0.1)
						{
							float nowS = GetGame().GetTickTime();
							// A stamp ahead of the clock is stale and never blocks.
							if (MCPCarDrive.s_LastAutoShiftS < 0.0 || nowS < MCPCarDrive.s_LastAutoShiftS || nowS - MCPCarDrive.s_LastAutoShiftS >= MCPCarDrive.AUTO_SHIFT_SETTLE_S)
							{
								ShiftUp();
								MCPCarDrive.s_LastAutoShiftS = nowS;
							}
						}
					}
					else if (GetGear() < firstGear || (throttle > 0.0 && spd < 5.0))
					{
						ShiftTo(firstGear);
					}
				}
				// An automatic keeps its mode until shifted. In P, R or N it
				// shifts up behind the same settle interval and is asked for
				// no throttle, so it gets throttle only once the mode reads D.
				else if (throttle > 0.0 && GearboxGetMode() != CarAutomaticGearboxMode.D)
				{
					float modeNowS = GetGame().GetTickTime();
					if (MCPCarDrive.s_LastAutoShiftS < 0.0 || modeNowS < MCPCarDrive.s_LastAutoShiftS || modeNowS - MCPCarDrive.s_LastAutoShiftS >= MCPCarDrive.AUTO_SHIFT_SETTLE_S)
					{
						ShiftUp();
						MCPCarDrive.s_LastAutoShiftS = modeNowS;
					}

					throttle = 0.0;
				}

				SetThrottle(throttle);
				SetSteering(MCPCarDrive.s_Steer);
				SetKickstand(0.0);
			}
		}
	}
}
