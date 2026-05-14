"use client";

import { useMemo } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

export function OpinionByYearChart(props: {
  summaryByYear: Record<string, { opinion_counts: Record<string, number> }>;
}) {
  const { pivoted, keys } = useMemo(() => {
    const years = Object.keys(props.summaryByYear).sort();
    const labels = new Set<string>();
    for (const y of years) {
      Object.keys(props.summaryByYear[y]?.opinion_counts || {}).forEach((l) => labels.add(l));
    }
    const labelList = Array.from(labels);
    const rows = years.map((y) => {
      const row: Record<string, string | number> = { year: y };
      const oc = props.summaryByYear[y]?.opinion_counts || {};
      for (const lab of labelList) row[lab] = oc[lab] || 0;
      return row;
    });
    return { pivoted: rows, keys: labelList };
  }, [props.summaryByYear]);

  if (!pivoted.length) {
    return (
      <div className="pill">
        연도별 감사의견 집계가 없습니다. ingest 후 export-dashboard를 실행하세요.
      </div>
    );
  }

  if (!keys.length) {
    return <div className="pill">감사의견 라벨이 집계되지 않았습니다(파싱 전이거나 매칭 실패).</div>;
  }

  const colors = ["#38bdf8", "#a78bfa", "#34d399", "#fb7185", "#fbbf24", "#94a3b8"];

  return (
    <div style={{ width: "100%", height: 320 }}>
      <ResponsiveContainer>
        <BarChart data={pivoted} margin={{ top: 10, right: 12, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#223056" />
          <XAxis dataKey="year" stroke="#94a3b8" />
          <YAxis stroke="#94a3b8" />
          <Tooltip
            contentStyle={{ background: "#0b1220", border: "1px solid #1f2a44", color: "#e2e8f0" }}
          />
          <Legend />
          {keys.map((k, i) => (
            <Bar key={k} dataKey={k} stackId="a" fill={colors[i % colors.length]} name={k} />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
