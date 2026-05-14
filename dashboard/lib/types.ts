export type SummaryPayload = {
  generatedAt: string;
  filings: Array<{
    rcept_no: string;
    corp_code: string;
    corp_name: string | null;
    stock_code: string | null;
    report_nm: string | null;
    rcept_dt: string | null;
    pblntf_detail_ty: string | null;
    opinion_label: string | null;
    kam_count: number | null;
    auditor_firm: string | null;
    opinion_modification_reason: string | null;
    accounting_standard: string | null;
    auditor_name: string | null;
    cpa_partner_name: string | null;
    emphasis_of_matter_present: number | null;
    emphasis_of_matter_content: string | null;
    other_matters_present: number | null;
    other_matters_content: string | null;
    filing_year?: string | null;
  }>;
  summaryByYear: Record<
    string,
    {
      filings: number;
      opinion_counts: Record<string, number>;
      kam_avg: number | null;
      kam_median: number | null;
    }
  >;
  kamItemsSample: Array<{
    rcept_no: string;
    corp_name: string | null;
    rcept_dt: string | null;
    ordinal: number;
    title: string | null;
    body_snippet: string | null;
    kam_content: string | null;
    selection_reason: string | null;
  }>;
  ae00024Sample: Array<{
    corp_code: string;
    bsns_year: string;
    status: string;
    message: string | null;
    fetched_at: string | null;
  }>;
};
