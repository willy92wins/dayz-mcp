// Hidden mission-session dialog host. One layout, six prebuilt rows.
// CreateWidgets runs once from the mission tick after the client is in-game.

class MCPDialogSink
{
	void OnDialogResult(MCPDialogResult dialog)
	{
	}
};

class MCPDialogSpec
{
	string kind;
	string title;
	string message;
	float timeout_s;
	ref array<ref MCPDialogField> fields;

	void MCPDialogSpec()
	{
		fields = new array<ref MCPDialogField>();
	}
};

class MCPDialogController : ScriptedWidgetEventHandler
{
	protected const int STATE_IDLE = 0;
	protected const int STATE_OPEN = 1;
	protected const int STATE_TERMINAL = 2;
	protected const int MAX_FIELDS = 6;
	static const string LAYOUT_PATH = "DayZ_MCP/gui/layouts/mcp_dialog.layout";

	protected int m_State;
	protected string m_Kind;
	protected int m_FieldCount;
	protected float m_NowS;
	protected float m_OpenedNowS;
	protected float m_DeadlineNowS;
	protected float m_OpenedTickS;
	protected float m_DeadlineTickS;
	protected string m_LastHostError;
	protected bool m_ControlsLocked;
	protected bool m_FocusLocked;
	protected bool m_HasTickDeadline;

	protected Widget m_Root;
	protected TextWidget m_Title;
	protected MultilineTextWidget m_Message;
	protected TextWidget m_Error;
	protected ButtonWidget m_BtnOk;
	protected ButtonWidget m_BtnYes;
	protected ButtonWidget m_BtnNo;
	protected ButtonWidget m_BtnSubmit;
	protected ButtonWidget m_BtnCancel;
	protected ref array<Widget> m_Rows;
	protected ref array<TextWidget> m_Labels;
	protected ref array<EditBoxWidget> m_Edits;
	protected ref array<string> m_FieldIds;
	protected ref array<bool> m_FieldRequired;

	protected MCPDialogSink m_Sink;
	protected MCPJobRunner m_Clock;
	protected MCPDialogResult m_ResultTarget;

	void MCPDialogController()
	{
		m_State = STATE_IDLE;
		m_Kind = "";
		m_FieldCount = 0;
		m_NowS = 0.0;
		m_OpenedNowS = 0.0;
		m_DeadlineNowS = 0.0;
		m_OpenedTickS = 0.0;
		m_DeadlineTickS = 0.0;
		m_LastHostError = "";
		m_ControlsLocked = false;
		m_FocusLocked = false;
		m_HasTickDeadline = false;
		m_Rows = new array<Widget>();
		m_Labels = new array<TextWidget>();
		m_Edits = new array<EditBoxWidget>();
		m_FieldIds = new array<string>();
		m_FieldRequired = new array<bool>();
	}

	void ~MCPDialogController()
	{
		if (GetGame())
		{
			UnlockInput();
		}

		DestroyHost();
		m_Sink = null;
		m_Clock = null;
		m_ResultTarget = null;
	}

	void SetSink(MCPDialogSink sink)
	{
		m_Sink = sink;
	}

	void SetClock(MCPJobRunner clock)
	{
		m_Clock = clock;
	}

	void SetResultTarget(MCPDialogResult target)
	{
		m_ResultTarget = target;
	}

	bool IsOpen()
	{
		return m_State == STATE_OPEN;
	}

	bool HasHost()
	{
		return m_Root != null;
	}

	string GetLastHostError()
	{
		return m_LastHostError;
	}

	// Workspace is touched here, once, after the mission is running.
	// Same GetWorkspace site as ResolveUiRoot.
	bool EnsureHost()
	{
		if (m_Root)
		{
			m_LastHostError = "";
			return true;
		}

		if (!GetGame())
		{
			m_LastHostError = "no_game";
			Print(string.Format("[MCP-DIALOG] host skipped reason=%1", m_LastHostError));
			return false;
		}

		WorkspaceWidget workspace = GetGame().GetWorkspace();
		if (!workspace)
		{
			m_LastHostError = "no_workspace";
			Print(string.Format("[MCP-DIALOG] host skipped reason=%1", m_LastHostError));
			return false;
		}

		m_Root = workspace.CreateWidgets(LAYOUT_PATH);
		if (!m_Root)
		{
			m_LastHostError = "host_create_failed";
			Print(string.Format("[MCP-DIALOG] host skipped reason=%1", m_LastHostError));
			return false;
		}

		if (!CacheWidgets())
		{
			Print(string.Format("[MCP-DIALOG] host skipped reason=%1", m_LastHostError));
			DestroyHost();
			return false;
		}

		// ui_click resolves handlers through GetScript/GetUserData, not through
		// SetHandler; the same instance is exposed as user data so the verb can
		// reach OnClick. Physical clicks still arrive through SetHandler.
		m_Root.SetHandler(this);
		m_Root.SetUserData(this);
		BindButton(m_BtnOk);
		BindButton(m_BtnYes);
		BindButton(m_BtnNo);
		BindButton(m_BtnSubmit);
		BindButton(m_BtnCancel);
		m_Root.Show(false);
		m_Root.SetFlags(WidgetFlags.IGNOREPOINTER);
		m_LastHostError = "";
		Print(string.Format("[MCP-DIALOG] host created path=%1", LAYOUT_PATH));
		return true;
	}

	bool Open(MCPDialogSpec spec, float deadlineNow)
	{
		if (m_State == STATE_OPEN)
		{
			return false;
		}

		if (!spec)
		{
			return false;
		}

		if (!EnsureHost())
		{
			return false;
		}

		m_Kind = spec.kind;
		m_DeadlineNowS = deadlineNow;
		m_OpenedNowS = ReadNow();
		m_NowS = m_OpenedNowS;
		m_HasTickDeadline = false;
		if (GetGame())
		{
			m_OpenedTickS = GetGame().GetTickTime();
			m_DeadlineTickS = m_OpenedTickS + spec.timeout_s;
			m_HasTickDeadline = true;
		}

		ResetWidgets();
		ApplySpec(spec);
		LockInput();
		m_Root.Show(true);
		m_Root.ClearFlags(WidgetFlags.IGNOREPOINTER);
		FocusDefault();
		m_State = STATE_OPEN;
		string idList = "";
		int idIndex = 0;
		while (idIndex < m_FieldCount)
		{
			if (idIndex > 0)
			{
				idList = idList + ",";
			}

			idList = idList + m_FieldIds.Get(idIndex);
			idIndex = idIndex + 1;
		}

		Print(string.Format("[MCP-DIALOG] open kind=%1 field_count=%2 ids=%3", m_Kind, m_FieldCount, idList));
		return true;
	}

	void Tick(float now)
	{
		m_NowS = now;
		if (m_State != STATE_OPEN)
		{
			return;
		}

		if (DeadlineReached(now))
		{
			TryFinish("timed_out", "", "", now);
		}
	}

	void FinishDisconnected()
	{
		if (m_State != STATE_OPEN)
		{
			UnlockInput();
			HideRoot();
			return;
		}

		float now = ReadNow();
		TryFinish("disconnected", "", "", now);
	}

	void DestroyHost()
	{
		UnlockInput();
		if (m_Root)
		{
			m_Root.Unlink();
		}

		m_Root = null;
		m_Title = null;
		m_Message = null;
		m_Error = null;
		m_BtnOk = null;
		m_BtnYes = null;
		m_BtnNo = null;
		m_BtnSubmit = null;
		m_BtnCancel = null;
		if (m_Rows)
		{
			m_Rows.Clear();
		}

		if (m_Labels)
		{
			m_Labels.Clear();
		}

		if (m_Edits)
		{
			m_Edits.Clear();
		}

		m_State = STATE_IDLE;
	}

	override bool OnClick(Widget w, int x, int y, int button)
	{
		if (m_State != STATE_OPEN)
		{
			return false;
		}

		if (!w)
		{
			return false;
		}

		if (button != 0)
		{
			return false;
		}

		float now = ReadNow();
		if (DeadlineReached(now))
		{
			TryFinish("timed_out", "", "", now);
			return true;
		}

		if (w == m_BtnOk)
		{
			TryFinish("completed", "ok", "", now);
			return true;
		}

		if (w == m_BtnYes)
		{
			TryFinish("completed", "", "yes", now);
			return true;
		}

		if (w == m_BtnNo)
		{
			TryFinish("completed", "", "no", now);
			return true;
		}

		if (w == m_BtnCancel)
		{
			TryFinish("cancelled", "", "", now);
			return true;
		}

		if (w == m_BtnSubmit)
		{
			if (!ValidateRequired())
			{
				ShowError(true);
				return true;
			}

			FillFormValues();
			TryFinish("completed", "", "", now);
			return true;
		}

		return false;
	}

	protected bool TryFinish(string state, string dismissedBy, string choice, float now)
	{
		if (m_State != STATE_OPEN)
		{
			return false;
		}

		string finalState = state;
		string finalDismissed = dismissedBy;
		string finalChoice = choice;
		if (DeadlineReached(now))
		{
			finalState = "timed_out";
			finalDismissed = "";
			finalChoice = "";
		}

		m_State = STATE_TERMINAL;
		if (m_ResultTarget)
		{
			m_ResultTarget.state = finalState;
			m_ResultTarget.dismissed_by = finalDismissed;
			m_ResultTarget.choice = finalChoice;
			m_ResultTarget.reason = "";
			m_ResultTarget.elapsed_s = ElapsedS(now);
			if (finalState != "completed")
			{
				if (m_ResultTarget.values)
				{
					m_ResultTarget.values.Clear();
				}
			}
		}

		HideRoot();
		UnlockInput();
		ResetWidgets();
		int valueCount = 0;
		if (m_ResultTarget && m_ResultTarget.values)
		{
			valueCount = m_ResultTarget.values.Count();
		}

		Print(string.Format("[MCP-DIALOG] finish state=%1 elapsed_s=%2 value_count=%3", finalState, ElapsedS(now), valueCount));
		if (m_Sink && m_ResultTarget)
		{
			m_Sink.OnDialogResult(m_ResultTarget);
		}

		m_State = STATE_IDLE;
		return true;
	}

	protected bool FailMissing(string name)
	{
		m_LastHostError = "missing_widget:" + name;
		return false;
	}

	protected bool CacheWidgets()
	{
		TextWidget titleW;
		if (!Class.CastTo(titleW, m_Root.FindAnyWidget("Title")))
		{
			return FailMissing("Title");
		}

		MultilineTextWidget messageW;
		if (!Class.CastTo(messageW, m_Root.FindAnyWidget("Message")))
		{
			return FailMissing("Message");
		}

		TextWidget errorW;
		if (!Class.CastTo(errorW, m_Root.FindAnyWidget("Error")))
		{
			return FailMissing("Error");
		}

		ButtonWidget btnOk;
		ButtonWidget btnYes;
		ButtonWidget btnNo;
		ButtonWidget btnSubmit;
		ButtonWidget btnCancel;
		if (!Class.CastTo(btnOk, m_Root.FindAnyWidget("BtnOk")))
		{
			return FailMissing("BtnOk");
		}

		if (!Class.CastTo(btnYes, m_Root.FindAnyWidget("BtnYes")))
		{
			return FailMissing("BtnYes");
		}

		if (!Class.CastTo(btnNo, m_Root.FindAnyWidget("BtnNo")))
		{
			return FailMissing("BtnNo");
		}

		if (!Class.CastTo(btnSubmit, m_Root.FindAnyWidget("BtnSubmit")))
		{
			return FailMissing("BtnSubmit");
		}

		if (!Class.CastTo(btnCancel, m_Root.FindAnyWidget("BtnCancel")))
		{
			return FailMissing("BtnCancel");
		}

		m_Title = titleW;
		m_Message = messageW;
		m_Error = errorW;
		m_BtnOk = btnOk;
		m_BtnYes = btnYes;
		m_BtnNo = btnNo;
		m_BtnSubmit = btnSubmit;
		m_BtnCancel = btnCancel;
		m_Rows.Clear();
		m_Labels.Clear();
		m_Edits.Clear();
		int i = 0;
		while (i < MAX_FIELDS)
		{
			string rowName = "Row" + i.ToString();
			string labelName = "Label" + i.ToString();
			string editName = "Edit" + i.ToString();
			Widget rowW = m_Root.FindAnyWidget(rowName);
			TextWidget labelW;
			EditBoxWidget editW;
			if (!rowW)
			{
				return FailMissing(rowName);
			}

			if (!Class.CastTo(labelW, m_Root.FindAnyWidget(labelName)))
			{
				return FailMissing(labelName);
			}

			if (!Class.CastTo(editW, m_Root.FindAnyWidget(editName)))
			{
				return FailMissing(editName);
			}

			m_Rows.Insert(rowW);
			m_Labels.Insert(labelW);
			m_Edits.Insert(editW);
			i = i + 1;
		}

		return true;
	}

	protected void BindButton(ButtonWidget button)
	{
		if (button)
		{
			button.SetHandler(this);
			button.SetUserData(this);
		}
	}

	protected void ApplySpec(MCPDialogSpec spec)
	{
		if (m_Title)
		{
			m_Title.SetText(spec.title);
		}

		if (m_Message)
		{
			m_Message.SetText(spec.message);
		}

		ShowError(false);
		HideAllButtons();
		HideAllRows();
		m_FieldIds.Clear();
		m_FieldRequired.Clear();
		m_FieldCount = 0;
		if (spec.kind == "acknowledge")
		{
			if (m_BtnOk)
			{
				m_BtnOk.Show(true);
			}
		}
		else if (spec.kind == "confirm")
		{
			if (m_BtnYes)
			{
				m_BtnYes.Show(true);
			}

			if (m_BtnNo)
			{
				m_BtnNo.Show(true);
			}

			if (m_BtnCancel)
			{
				m_BtnCancel.Show(true);
			}
		}
		else if (spec.kind == "form")
		{
			if (m_BtnSubmit)
			{
				m_BtnSubmit.Show(true);
			}

			if (m_BtnCancel)
			{
				m_BtnCancel.Show(true);
			}

			ApplyFields(spec.fields);
		}
	}

	protected void ApplyFields(array<ref MCPDialogField> fields)
	{
		if (!fields)
		{
			return;
		}

		int count = fields.Count();
		if (count > MAX_FIELDS)
		{
			count = MAX_FIELDS;
		}

		int i = 0;
		while (i < count)
		{
			MCPDialogField field = fields.Get(i);
			if (field)
			{
				m_FieldIds.Insert(field.id);
				m_FieldRequired.Insert(field.required);
				Widget rowW = m_Rows.Get(i);
				TextWidget labelW = m_Labels.Get(i);
				EditBoxWidget editW = m_Edits.Get(i);
				if (rowW)
				{
					rowW.Show(true);
				}

				if (labelW)
				{
					labelW.SetText(field.label);
				}

				if (editW)
				{
					editW.SetText(field.default_text);
				}

				m_FieldCount = m_FieldCount + 1;
			}

			i = i + 1;
		}
	}

	protected void ResetWidgets()
	{
		if (m_Title)
		{
			m_Title.SetText("");
		}

		if (m_Message)
		{
			m_Message.SetText("");
		}

		ShowError(false);
		HideAllButtons();
		int i = 0;
		while (i < m_Edits.Count())
		{
			EditBoxWidget editW = m_Edits.Get(i);
			if (editW)
			{
				editW.SetText("");
			}

			TextWidget labelW = m_Labels.Get(i);
			if (labelW)
			{
				labelW.SetText("");
			}

			Widget rowW = m_Rows.Get(i);
			if (rowW)
			{
				rowW.Show(false);
			}

			i = i + 1;
		}

		m_FieldIds.Clear();
		m_FieldRequired.Clear();
		m_FieldCount = 0;
	}

	protected void HideAllButtons()
	{
		if (m_BtnOk)
		{
			m_BtnOk.Show(false);
		}

		if (m_BtnYes)
		{
			m_BtnYes.Show(false);
		}

		if (m_BtnNo)
		{
			m_BtnNo.Show(false);
		}

		if (m_BtnSubmit)
		{
			m_BtnSubmit.Show(false);
		}

		if (m_BtnCancel)
		{
			m_BtnCancel.Show(false);
		}
	}

	protected void HideAllRows()
	{
		int i = 0;
		while (i < m_Rows.Count())
		{
			Widget rowW = m_Rows.Get(i);
			if (rowW)
			{
				rowW.Show(false);
			}

			i = i + 1;
		}
	}

	protected void ShowError(bool visible)
	{
		if (m_Error)
		{
			if (visible)
			{
				m_Error.SetText("Required field is empty");
			}
			else
			{
				m_Error.SetText("");
			}

			m_Error.Show(visible);
		}
	}

	protected bool ValidateRequired()
	{
		int i = 0;
		while (i < m_FieldCount)
		{
			bool required = m_FieldRequired.Get(i);
			if (required)
			{
				string text = "";
				EditBoxWidget editW = m_Edits.Get(i);
				if (editW)
				{
					text = editW.GetText();
				}

				if (text == "")
				{
					return false;
				}
			}

			i = i + 1;
		}

		return true;
	}

	protected void FillFormValues()
	{
		if (!m_ResultTarget)
		{
			return;
		}

		if (!m_ResultTarget.values)
		{
			return;
		}

		m_ResultTarget.values.Clear();
		int i = 0;
		while (i < m_FieldCount)
		{
			MCPDialogValue item = new MCPDialogValue();
			item.id = m_FieldIds.Get(i);
			item.value = "";
			EditBoxWidget editW = m_Edits.Get(i);
			if (editW)
			{
				item.value = editW.GetText();
			}

			m_ResultTarget.values.Insert(item);
			i = i + 1;
		}
	}

	protected void FocusDefault()
	{
		Widget focusW;
		focusW = null;
		if (m_Kind == "form" && m_FieldCount > 0)
		{
			focusW = m_Edits.Get(0);
		}
		else if (m_Kind == "acknowledge")
		{
			focusW = m_BtnOk;
		}
		else if (m_Kind == "confirm")
		{
			focusW = m_BtnYes;
		}
		else
		{
			focusW = m_BtnSubmit;
		}

		if (focusW)
		{
			SetFocus(focusW);
		}
	}

	protected void HideRoot()
	{
		SetFocus(null);
		if (m_Root)
		{
			m_Root.Show(false);
			m_Root.SetFlags(WidgetFlags.IGNOREPOINTER);
		}
	}

	protected void LockInput()
	{
#ifndef SERVER
		if (!GetGame())
		{
			return;
		}

		UIManager uiMgr = GetGame().GetUIManager();
		if (uiMgr)
		{
			uiMgr.ShowUICursor(true);
		}

		if (!m_FocusLocked)
		{
			Input inp = GetGame().GetInput();
			if (inp)
			{
				inp.ChangeGameFocus(1);
			}

			m_FocusLocked = true;
		}

		if (!m_ControlsLocked)
		{
			Mission mission = GetGame().GetMission();
			if (mission)
			{
				mission.PlayerControlDisable(INPUT_EXCLUDE_ALL);
				m_ControlsLocked = true;
			}
		}
#endif
	}

	protected void UnlockInput()
	{
#ifndef SERVER
		if (m_ControlsLocked)
		{
			if (GetGame())
			{
				Mission mission = GetGame().GetMission();
				if (mission)
				{
					mission.PlayerControlEnable(false);
				}
			}

			m_ControlsLocked = false;
		}

		if (GetGame())
		{
			UIManager uiMgr = GetGame().GetUIManager();
			if (uiMgr)
			{
				uiMgr.ShowUICursor(false);
			}
		}

		if (m_FocusLocked)
		{
			if (GetGame())
			{
				Input inp = GetGame().GetInput();
				if (inp)
				{
					inp.ChangeGameFocus(-1);
				}
			}

			m_FocusLocked = false;
		}
#endif
	}

	protected float ReadNow()
	{
		if (m_Clock)
		{
			return m_Clock.GetElapsedS();
		}

		return m_NowS;
	}

	protected bool DeadlineReached(float now)
	{
		if (now >= m_DeadlineNowS)
		{
			return true;
		}

		if (m_HasTickDeadline && GetGame())
		{
			float tickNow = GetGame().GetTickTime();
			if (tickNow >= m_DeadlineTickS)
			{
				return true;
			}
		}

		return false;
	}

	protected float ElapsedS(float now)
	{
		float elapsed = now - m_OpenedNowS;
		if (m_HasTickDeadline && GetGame())
		{
			elapsed = GetGame().GetTickTime() - m_OpenedTickS;
		}

		if (elapsed < 0.0)
		{
			elapsed = 0.0;
		}

		return elapsed;
	}
};
