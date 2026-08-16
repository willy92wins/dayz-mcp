class MCPJobRunnerOwner
{
	bool MCP_ProcessJob(MCPJob job)
	{
		return false;
	}

	bool MCP_IsJobReady(MCPJob job)
	{
		return false;
	}

	void MCP_PostJobSuccess(MCPJob job)
	{
	}

	void MCP_PostJobFailure(MCPJob job)
	{
	}

	void MCP_PostJobTimeout(MCPJob job)
	{
	}

	void MCP_ClearJobRefs(MCPJob job)
	{
	}
};

class MCPJobRunner
{
	protected ref map<int, ref MCPJob> m_Jobs;
	protected float m_ElapsedS;

	void MCPJobRunner()
	{
		m_Jobs = new map<int, ref MCPJob>();
		m_ElapsedS = 0.0;
	}

	float GetElapsedS()
	{
		return m_ElapsedS;
	}

	int Count()
	{
		if (!m_Jobs)
		{
			return 0;
		}

		return m_Jobs.Count();
	}

	void AddJob(MCPJob job)
	{
		if (!m_Jobs || !job)
		{
			return;
		}

		m_Jobs.Insert(job.id, job);
	}

	void Tick(float timeslice, MCPJobRunnerOwner owner)
	{
		m_ElapsedS = m_ElapsedS + timeslice;
		ProcessJobs(owner);
	}

	void Clear()
	{
		if (m_Jobs)
		{
			m_Jobs.Clear();
		}
	}

	protected void ProcessJobs(MCPJobRunnerOwner owner)
	{
		if (!m_Jobs || !owner)
		{
			return;
		}

		int i = m_Jobs.Count() - 1;
		while (i >= 0)
		{
			int jobId = m_Jobs.GetKey(i);
			MCPJob job = m_Jobs.GetElement(i);
			if (!job)
			{
				m_Jobs.Remove(jobId);
			}
			else
			{
				if (owner.MCP_ProcessJob(job) || owner.MCP_IsJobReady(job))
				{
					if (job.error != "")
					{
						owner.MCP_PostJobFailure(job);
					}
					else
					{
						owner.MCP_PostJobSuccess(job);
					}
					owner.MCP_ClearJobRefs(job);
					m_Jobs.Remove(jobId);
				}
				else if (m_ElapsedS > job.deadline_s)
				{
					owner.MCP_PostJobTimeout(job);
					owner.MCP_ClearJobRefs(job);
					m_Jobs.Remove(jobId);
				}
			}

			i = i - 1;
		}
	}
};
