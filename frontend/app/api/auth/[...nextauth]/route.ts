import NextAuth, { NextAuthOptions } from "next-auth";
import { MongoDBAdapter } from "@auth/mongodb-adapter";
import clientPromise from "@/lib/mongodb";

export const authOptions: NextAuthOptions = {
    adapter: MongoDBAdapter(clientPromise) as any,
    providers: [
        {
            id: "hackclub",
            name: "Hack Club",
            type: "oauth",
            authorization: "https://auth.hackclub.com/oauth/authorize",
            token: "https://auth.hackclub.com/oauth/token",
            userinfo: "https://auth.hackclub.com/api/v1/me",
            clientId: process.env.HACKCLUB_CLIENT_ID,
            clientSecret: process.env.HACKCLUB_CLIENT_SECRET,
            profile(profile) {
                return {
                    id: profile.id.toString(),
                    name: profile.name || profile.username,
                    email: profile.email,
                    image: profile.avatar || profile.photo,
                };
            },
        },
    ],
    session: {
        strategy: "database", // Use MongoDB to store sessions
    },
    callbacks: {
        async session({ session, user }) {
            if (session.user) {
                // Ensure the user ID is passed to the frontend session
                (session.user as any).id = user.id;
            }
            return session;
        },
    },
    pages: {
        // You can specify a custom sign-in page here if needed
    },
    debug: process.env.NODE_ENV === "development",
};

const handler = NextAuth(authOptions);

export { handler as GET, handler as POST };
