export { default } from "next-auth/middleware";

export const config = {
  matcher: ["/create", "/project/:path*", "/settings"],
};
