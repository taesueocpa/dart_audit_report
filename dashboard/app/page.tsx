"use client";

import { useEffect, useMemo, useState } from "react";
import type { SummaryPayload } from "@/lib/types";
import { OpinionByYearChart } from "@/components/OpinionByYearChart";

function formatRceptDt(raw: string | null | undefined): string {
  if (!raw) return "—";
  const s = String(raw).trim();
  if (/^\d{8}$/.test(s)) {
    return `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6, 8)}`;
  }
  if (/^\d{4}-\d{2}-\d{2}/.test(s)) {
    return s.slice(0, 10);
  }
  return s;
}

async function loadSummary(): Promise<SummaryPayload> {
  const res = await fetch(`${process.env.NEXT_PUBLIC_BASE_PATH || ""}/data/summary.json`, {
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error(`summary.json 로드 실패 (${res.status})`);
  }
  return (await res.json()) as SummaryPayload;
}

export default function HomePage() {
  const [data, setData] = useState<SummaryPayload | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [q, setQ] = useState("");

  useEffect(() => {
    loadSummary()
      .then(setData)
      .catch((e: unknown) => setErr(e instanceof Error ? e.message : String(e)));
  }, []);

  const filtered = useMemo(() => {
    const rows = data?.filings || [];
    const qq = q.trim().toLowerCase();
    if (!qq) return rows.slice(0, 200);
    return rows
      .filter((r) => {
        const hay = `${r.corp_name || ""} ${r.stock_code || ""} ${r.report_nm || ""}`.toLowerCase();
        return hay.includes(qq);
      })
      .slice(0, 400);
  }, [data, q]);

  return (
    <main>
      <h1>DART 감사보고서 · 핵심감사사항(KAM) 요약</h1>
      <p className="sub">
        이 페이지는 <a href="https://opendart.fss.or.kr/">전자공시 OPENDART</a>에서 수집한 공시 원본을
        로컬에서 파싱·집계한 결과를 보여줍니다. 감사의견·KAM은 자동 추출 휴리스틱이므로 참고용이며
        법적·회계적 판단을 대체하지 않습니다.
      </p>

      {err ? (
        <div className="card">
          <h2>데이터 로드 오류</h2>
          <div className="pill">{err}</div>
          <p className="footer" style={{ marginTop: 12 }}>
            `ingest`에서 `dart-kam export-dashboard`를 실행해 `dashboard/public/data/summary.json`을 생성한 뒤
            `npm run build`를 다시 실행하세요.
          </p>
        </div>
      ) : null}

      {data ? (
        <>
          <div className="grid">
            <div className="card">
              <h2>연도별 감사의견 분포(파싱 결과)</h2>
              <OpinionByYearChart summaryByYear={data.summaryByYear} />
            </div>
            <div className="card">
              <h2>연도별 요약</h2>
              <table className="table">
                <thead>
                  <tr>
                    <th>연도</th>
                    <th>공시 건수</th>
                    <th>KAM 평균</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.keys(data.summaryByYear)
                    .sort()
                    .map((y) => {
                      const s = data.summaryByYear[y];
                      return (
                        <tr key={y}>
                          <td>{y}</td>
                          <td>{s.filings}</td>
                          <td>{s.kam_avg == null ? "—" : s.kam_avg.toFixed(2)}</td>
                        </tr>
                      );
                    })}
                </tbody>
              </table>
              <div className="footer">생성 시각(UTC): {data.generatedAt}</div>
            </div>
          </div>

          <div className="card" style={{ marginTop: 14 }}>
            <h2>회사 검색</h2>
            <input
              type="search"
              placeholder="회사명 또는 종목코드로 검색…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
            <div style={{ height: 12 }} />
            <table className="table">
              <thead>
                <tr>
                  <th>접수일</th>
                  <th>회사</th>
                  <th>의견(추정)</th>
                  <th>회계기준</th>
                  <th>감사인</th>
                  <th>담당CPA</th>
                  <th>강조</th>
                  <th>기타</th>
                  <th>KAM</th>
                  <th>공시</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((r) => (
                  <tr key={r.rcept_no}>
                    <td>{formatRceptDt(r.rcept_dt)}</td>
                    <td>
                      <div style={{ fontWeight: 650 }}>{r.corp_name}</div>
                      {r.stock_code ? (
                        <div className="footer" style={{ marginTop: 4 }}>
                          종목 {r.stock_code}
                        </div>
                      ) : null}
                    </td>
                    <td>
                      <div>{r.opinion_label || "—"}</div>
                      {r.opinion_modification_reason ? (
                        <div className="footer" style={{ marginTop: 6 }} title={r.opinion_modification_reason}>
                          사유: {(r.opinion_modification_reason || "").slice(0, 120)}
                          {(r.opinion_modification_reason || "").length > 120 ? "…" : ""}
                        </div>
                      ) : null}
                    </td>
                    <td className="footer">{r.accounting_standard || "—"}</td>
                    <td className="footer" title={r.auditor_firm || r.auditor_name || ""}>
                      {(r.auditor_firm || r.auditor_name || "—").slice(0, 48)}
                    </td>
                    <td className="footer">{r.cpa_partner_name || "—"}</td>
                    <td className="footer">{r.emphasis_of_matter_present === 1 ? "Y" : "—"}</td>
                    <td className="footer">{r.other_matters_present === 1 ? "Y" : "—"}</td>
                    <td>{r.kam_count == null ? "—" : String(r.kam_count)}</td>
                    <td className="footer">{r.report_nm}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="card" style={{ marginTop: 14 }}>
            <h2>핵심감사사항 본문(전체 텍스트 DB 저장, 샘플 일부)</h2>
            <p className="footer" style={{ marginTop: 0 }}>
              항목별 제목·선정사유 분리 없이 &quot;핵심감사사항&quot; 절 전체를 저장합니다. 긴 행은 마우스를 올리면
              앞부분이 title 속성으로 더 보입니다.
            </p>
            <table className="table">
              <thead>
                <tr>
                  <th>접수일</th>
                  <th>회사</th>
                  <th>본문(앞부분)</th>
                </tr>
              </thead>
              <tbody>
                {(data.kamItemsSample || []).slice(0, 80).map((k, idx) => {
                  const full = k.kam_content || k.body_snippet || "";
                  return (
                    <tr key={`${k.rcept_no}-${k.ordinal}-${idx}`}>
                      <td>{formatRceptDt(k.rcept_dt)}</td>
                      <td>{k.corp_name}</td>
                      <td className="footer" title={full.slice(0, 4000)}>
                        {full ? `${full.slice(0, 220)}${full.length > 220 ? "…" : ""}` : "—"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <div className="card" style={{ marginTop: 14 }}>
            <h2>강조사항·기타사항 본문 샘플(파싱된 공시 상위 일부)</h2>
            <p className="footer" style={{ marginTop: 0 }}>
              표에서 Y인 행의 전체 문단은 DB·summary.json에 저장되어 있습니다(휴리스틱 추출).
            </p>
            <table className="table">
              <thead>
                <tr>
                  <th>접수일</th>
                  <th>회사</th>
                  <th>강조사항</th>
                  <th>기타사항</th>
                </tr>
              </thead>
              <tbody>
                {(data.filings || [])
                  .filter(
                    (r) => r.emphasis_of_matter_present === 1 || r.other_matters_present === 1,
                  )
                  .slice(0, 40)
                  .map((r) => (
                    <tr key={`eo-${r.rcept_no}`}>
                      <td>{formatRceptDt(r.rcept_dt)}</td>
                      <td>{r.corp_name}</td>
                      <td className="footer" title={r.emphasis_of_matter_content || ""}>
                        {r.emphasis_of_matter_present === 1
                          ? `${(r.emphasis_of_matter_content || "").slice(0, 160)}${
                              (r.emphasis_of_matter_content || "").length > 160 ? "…" : ""
                            }`
                          : "—"}
                      </td>
                      <td className="footer" title={r.other_matters_content || ""}>
                        {r.other_matters_present === 1
                          ? `${(r.other_matters_content || "").slice(0, 160)}${
                              (r.other_matters_content || "").length > 160 ? "…" : ""
                            }`
                          : "—"}
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>

          <div className="card" style={{ marginTop: 14 }}>
            <h2>AE00024(감사인·감사의견) 캐시 샘플</h2>
            <p className="footer" style={{ marginTop: 0 }}>
              정기보고서 기반 구조화 API로 파싱 결과를 교차검증할 때 사용합니다(연간 11011 조회).
            </p>
            <table className="table">
              <thead>
                <tr>
                  <th>회계연도</th>
                  <th>corp_code</th>
                  <th>status</th>
                  <th>message</th>
                </tr>
              </thead>
              <tbody>
                {(data.ae00024Sample || []).slice(0, 40).map((r) => (
                  <tr key={`${r.corp_code}-${r.bsns_year}`}>
                    <td>{r.bsns_year}</td>
                    <td>{r.corp_code}</td>
                    <td>{r.status}</td>
                    <td className="footer">{r.message}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : !err ? (
        <div className="pill">불러오는 중…</div>
      ) : null}

      <p className="footer" style={{ marginTop: 18 }}>
        출처: 금융감독원 전자공시시스템(DART/OPENDART). 무단 크롤링·재배포는 각 서비스 약관을 따르세요.
      </p>
    </main>
  );
}
