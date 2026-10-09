// action_cursor snapshot. The HUD updates the cursor; this file only reads
// that instance afterwards. It does not call Update, Can, or action execution.

class MCPFrame
{
	protected static int s_Tick;

	static void Advance()
	{
		s_Tick = s_Tick + 1;
	}

	static int Tick()
	{
		return s_Tick;
	}
};

modded class IngameHud
{
	ActionTargetsCursor MCP_ActionTargetsCursor()
	{
		return m_ActionTargetsCursor;
	}
};

modded class ActionTargetsCursor
{
	protected int m_MCPContentTick;
	protected string m_MCPItemDesc;
	protected bool m_MCPRefreshSeen;

	int MCP_ContentTick()
	{
		return m_MCPContentTick;
	}

	string MCP_Readiness()
	{
		if (!m_Root)
		{
			return "cursor_unavailable";
		}
		if (!m_AM)
		{
			return "no_action_manager";
		}
		return "";
	}

	override void GetTarget()
	{
		// Vanilla reaches GetTarget only after the vision-obstruction and
		// HUD-hide returns. Stamping Update's return would mark retained
		// content fresh.
		m_MCPRefreshSeen = true;
		super.GetTarget();
	}

#ifdef DAYZ_1_29
	override void Update()
	{
		m_MCPRefreshSeen = false;
		super.Update();
#else
	// 1.30 added the fullUpdate parameter to the vanilla signature.
	override void Update(bool fullUpdate = true)
	{
		m_MCPRefreshSeen = false;
		super.Update(fullUpdate);
#endif
		if (!m_MCPRefreshSeen)
		{
			return;
		}
		if (!m_Player || !m_Player.IsPlayerSelected() || !m_AM || !m_Hud)
		{
			return;
		}
		if (m_Hud.GetHudVisibility().IsContextFlagActive(IngameHudVisibility.HUD_HIDE_FLAGS))
		{
			return;
		}
		m_MCPContentTick = MCPFrame.Tick();
	}

	override void SetItemDesc(string descText, int cargoCount, string itemWidget, string descWidget)
	{
		m_MCPItemDesc = descText;
		super.SetItemDesc(descText, cargoCount, itemWidget, descWidget);
	}

	void MCP_Fill(MCPActionCursor snap, int readTick)
	{
		if (!snap)
		{
			return;
		}
		snap.read_tick = readTick;
		snap.cursor_update_tick = m_MCPContentTick;
		snap.item_description_input = m_MCPItemDesc;
		MCP_FillTarget(m_Target, snap.cursor_target);
		if (m_AM)
		{
			MCP_FillTarget(m_AM.FindActionTarget(), snap.manager_target);
			MCP_AddManagerSlot(snap, "primary", InteractActionInput, "InteractActionInput");
			MCP_AddManagerSlot(snap, "secondary", DefaultActionInput, "DefaultActionInput");
			MCP_AddManagerSlot(snap, "continuous_primary", ContinuousInteractActionInput, "ContinuousInteractActionInput");
			MCP_AddManagerSlot(snap, "continuous_secondary", ContinuousDefaultActionInput, "ContinuousDefaultActionInput");
		}
		MCP_AddCursorSlot(snap, "primary", m_Interact, m_InteractActionsNum, "InteractActionInput", "interact");
		MCP_AddCursorSlot(snap, "secondary", m_Single, m_ItemActionsNum, "DefaultActionInput", "single");
		MCP_AddCursorSlot(snap, "continuous_primary", m_ContinuousInteract, m_ContinuousInteractActionsNum, "ContinuousInteractActionInput", "continuous_interact");
		MCP_AddCursorSlot(snap, "continuous_secondary", m_Continuous, m_ContinuousItemActionsNum, "ContinuousDefaultActionInput", "continuous");
		MCP_FillRef(m_DisplayInteractTarget, snap.display_object);
		MCP_AddWidget(snap, "root", m_Root);
		if (m_Root)
		{
			MCP_AddWidget(snap, "item", m_Root.FindAnyWidget("item"));
			MCP_AddWidget(snap, "item_desc", m_Root.FindAnyWidget("item_desc"));
			MCP_AddWidget(snap, "item_flag_icon", m_Root.FindAnyWidget("item_flag_icon"));
			MCP_AddWidget(snap, "primary", m_Root.FindAnyWidget("interact"));
			MCP_AddWidget(snap, "secondary", m_Root.FindAnyWidget("single"));
			MCP_AddWidget(snap, "continuous_primary", m_Root.FindAnyWidget("continuous_interact"));
			MCP_AddWidget(snap, "continuous_secondary", m_Root.FindAnyWidget("continuous"));
		}
		else
		{
			MCP_AddWidget(snap, "item", null);
			MCP_AddWidget(snap, "item_desc", null);
			MCP_AddWidget(snap, "item_flag_icon", null);
			MCP_AddWidget(snap, "primary", null);
			MCP_AddWidget(snap, "secondary", null);
			MCP_AddWidget(snap, "continuous_primary", null);
			MCP_AddWidget(snap, "continuous_secondary", null);
		}
	}

	protected void MCP_AddManagerSlot(MCPActionCursor snap, string slot, typename inputType, string inputName)
	{
		MCPCursorSlot row;
		ActionBase action;
		row = new MCPCursorSlot();
		row.slot = slot;
		row.input_name = inputName;
		row.count = 0;
		row.action_class = "";
		row.widget = "";
		if (m_AM)
		{
			action = m_AM.GetPossibleAction(inputType);
			row.count = m_AM.GetPossibleActionCount(inputType);
			if (action)
			{
				row.action_class = action.Type().ToString();
			}
		}
		snap.manager_slots.Insert(row);
	}

	protected void MCP_AddCursorSlot(MCPActionCursor snap, string slot, ActionBase action, int count, string inputName, string widgetName)
	{
		MCPCursorSlot row;
		row = new MCPCursorSlot();
		row.slot = slot;
		row.input_name = inputName;
		row.widget = widgetName;
		row.count = count;
		row.action_class = "";
		if (action)
		{
			row.action_class = action.Type().ToString();
		}
		snap.cursor_slots.Insert(row);
	}

	protected void MCP_FillTarget(ActionTarget target, MCPCursorTarget dst)
	{
		vector hit;
		if (!dst)
		{
			return;
		}
		dst.component_index = -1;
		dst.cursor_pos.Clear();
		if (!target)
		{
			dst.present = false;
			dst.object_ref.present = false;
			dst.parent_ref.present = false;
			return;
		}
		dst.present = true;
		dst.component_index = target.GetComponentIndex();
		hit = target.GetCursorHitPos();
		dst.cursor_pos.Insert(hit[0]);
		dst.cursor_pos.Insert(hit[1]);
		dst.cursor_pos.Insert(hit[2]);
		MCP_FillRef(target.GetObject(), dst.object_ref);
		MCP_FillRef(target.GetParent(), dst.parent_ref);
	}

	protected void MCP_FillRef(Object subject, MCPCursorRef dst)
	{
		vector pos;
		int low;
		int high;
		if (!dst)
		{
			return;
		}
		dst.pos.Clear();
		dst.classname = "";
		dst.net_low = 0;
		dst.net_high = 0;
		if (!subject)
		{
			dst.present = false;
			return;
		}
		dst.present = true;
		dst.classname = subject.GetType();
		pos = subject.GetPosition();
		dst.pos.Insert(pos[0]);
		dst.pos.Insert(pos[1]);
		dst.pos.Insert(pos[2]);
		subject.GetNetworkID(low, high);
		dst.net_low = low;
		dst.net_high = high;
	}

	protected void MCP_AddWidget(MCPActionCursor snap, string name, Widget w)
	{
		MCPCursorWidget row;
		float sx;
		float sy;
		float sw;
		float sh;
		row = new MCPCursorWidget();
		row.name = name;
		row.exists = false;
		row.path = "";
		row.widget_type = "";
		row.visible = false;
		row.visible_hierarchy = false;
		if (w)
		{
			row.exists = true;
			row.path = MCP_WidgetPath(w);
			row.widget_type = w.GetTypeName();
			row.visible = w.IsVisible();
			row.visible_hierarchy = w.IsVisibleHierarchy();
			w.GetScreenPos(sx, sy);
			w.GetScreenSize(sw, sh);
			row.screen_x = sx;
			row.screen_y = sy;
			row.screen_w = sw;
			row.screen_h = sh;
		}
		snap.widgets.Insert(row);
	}

	protected string MCP_WidgetPath(Widget w)
	{
		string path;
		Widget cursor;
		int guard;
		if (!w)
		{
			return "";
		}
		if (w == m_Root)
		{
			return w.GetName();
		}
		path = w.GetName();
		cursor = w.GetParent();
		guard = 0;
		while (cursor && cursor != m_Root && guard < 12)
		{
			path = cursor.GetName() + "/" + path;
			cursor = cursor.GetParent();
			guard = guard + 1;
		}
		if (m_Root)
		{
			path = m_Root.GetName() + "/" + path;
		}
		return path;
	}
};
