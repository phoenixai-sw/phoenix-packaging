import type { ApiSchema } from "./api-contract";
export type ServiceCatalogItem = ApiSchema<"ServiceCatalogItem">;
export type ServiceQuote = ApiSchema<"ServiceQuotePayload">;
export type ServiceOrder = ApiSchema<"ServiceOrderPayload">;
export type ServiceCode = ServiceOrder["service_code"];
export type ServiceStatus = ServiceOrder["status"];
export const serviceStatus: Record<ServiceStatus, string> = { requested: "신청 접수", quoted: "견적 도착", accepted: "견적 수락", in_progress: "업무 진행", delivered: "결과 전달", completed: "업무 완료", canceled: "신청 취소", rejected: "진행 불가" };
